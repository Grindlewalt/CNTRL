"""nov2: orchestrator items on branches (docs/nov2-contract.md).

plan.py calls into here: the job's base, each item's start commit, what an item's copy
holds when it settles, and the integration step that merges the done items into one PR
branch (agent/orch-<job6>), runs a resolver for conflicts, and files the merge request.
Policy stays the project's; files follow the work key (worktrees.py)."""
from __future__ import annotations

import contextlib
import re

from . import bus, gitsafe, worktrees

MARKER = re.compile(r"^(<<<<<<<|>>>>>>>)", re.M)


def on(plan: dict) -> bool:
    return plan.get("isolation") == "branches"


def key_for(slug: str, plan: dict, item_id: str) -> str:
    return worktrees.work_key(slug, plan["work_job"], item_id)


async def ensure_base(slug: str, plan: dict, job_id: str) -> None:
    """At job start (under the plan lock): the base commit, once per plan."""
    if not on(plan) or (plan.get("base") and plan.get("work_job")):
        return
    plan["work_job"] = job_id
    sha = await worktrees.base_snapshot(slug, job_id)
    plan["base"] = {"sha": sha, "dirty": worktrees.last_dirty.get(worktrees.job_tag(job_id), 0)}


async def start_commit(slug: str, plan: dict, deps: list[dict]) -> tuple[str, str]:
    """(commit the item's copy starts from, a note for its task or ""): the base merged
    with the branches of the items it depends on."""
    gd = worktrees.resolve(slug).git_dir
    cur = plan["base"]["sha"]
    done = [d for d in deps if (d.get("work") or {}).get("sha") and d["status"] == "done"]
    for d in done:
        sha = d["work"]["sha"]
        if await worktrees.is_ancestor(gd, sha, cur):
            continue
        if await worktrees.is_ancestor(gd, cur, sha):
            cur = sha
            continue
        tree, conflicts = await worktrees.merge_tree(gd, cur, sha)
        if conflicts:
            return sha, (f"Note: the branches of the items you build on ({', '.join(x['id'] for x in done)}) "
                         f"conflict in {', '.join(conflicts[:5])}; your copy starts from {d['id']}'s "
                         "work only. Check what the others did before relying on it.")
        cur = await worktrees.commit_tree(gd, tree, [cur, sha], f"cntrl: start of copy (with {d['id']})")
    return cur, ""


def task_note(w: worktrees.Work, extra: str) -> str:
    return (f"\n# Your copy\nYou work in your own copy of the project (branch {w.branch}); other "
            "agents' changes are not visible here. The operator reviews one pull request at the "
            "end, so just do your item." + (f"\n{extra}" if extra else ""))


async def finish_item(slug: str, plan_job: str, key: str) -> dict:
    """The copy's state after an attempt: everything on the branch, how much changed."""
    w = worktrees.resolve(key)
    ref = f"refs/heads/{w.branch}"
    base = await gitsafe.rev(w.git_dir, ref)
    try:
        await gitsafe.snapshot(w.git_dir, w.dir, ref, f"cntrl {w.part}: end of attempt",
                               start=base)
    except Exception:  # noqa: BLE001 — recorded as it stands
        pass
    sha = await gitsafe.rev(w.git_dir, ref)
    return {"key": key, "branch": w.branch, "sha": sha, "state": "running"}


async def mark(plan: dict, it: dict) -> None:
    """After _settle (under the plan lock): the item's work record follows its status."""
    work = it.get("work")
    if not work:
        return
    st = {"done": "done", "todo": "running", "running": "running"}.get(it["status"], "failed")
    work["state"] = st if work.get("state") not in ("merged", "resolved", "conflict") else work["state"]
    if work.get("sha") and plan.get("base"):
        gd = worktrees.resolve(work["key"]).git_dir
        work["files"] = len(await worktrees.changed_files(gd, plan["base"]["sha"], work["sha"]))
    if st == "done" and "order" not in work:
        work["order"] = 1 + max([(x.get("work") or {}).get("order", 0) for x in plan["items"]] + [0])


def _pr_note(plan: dict) -> str:
    m = plan.get("merge") or {}
    if m.get("state") == "filed":
        return ("\n\nThe work is filed as one pull request for the operator "
                f"(branch {m.get('branch')}, request {m.get('request_id')}). The project is "
                "unchanged until it is approved.")
    if m.get("state") == "conflict":
        return ("\n\nSome items conflicted and could not be resolved; the pull request was "
                "filed without them. The project is unchanged until it is approved.")
    return ""


async def _resolve_conflict(slug: str, plan: dict, job_id: str, root_id: int, tip: str,
                            tree: str, conflicts: list[str], a: dict, b_title: str,
                            agents_run, plan_mod) -> str | None:
    """Run one resolver item on the merge copy. Returns the resolved tree or None."""
    wj = plan["work_job"]
    key = worktrees.work_key(slug, wj, "merge")
    await worktrees.remove(key, delete_branch=False)
    w = await worktrees.create(slug, wj, "merge", tip)
    await worktrees.reset_copy(key, tree)
    fake = {"id": "merge", "title": "resolve merge conflicts", "assignee": None, "model": None}
    agent = plan_mod._item_agent(plan, fake)
    task = (f"[merge] Resolve the conflict markers in {', '.join(conflicts)} between "
            f"\"{a['title']}\" and \"{b_title}\", keeping both intents. Run the tests if there "
            "are any. Remove every <<<<<<<, ======= and >>>>>>> marker, then call plan_report "
            f"with status done.{task_note(w, '')}")
    from . import orchestrator
    async with orchestrator.job_workspace(key, top_level=True):
        res = await agents_run._run_headless(
            agent, task, active=slug, job_id=job_id, title="[merge] resolve conflicts",
            extra_tools=("plan_report",), work_key=key)
    del res
    for p in conflicts:
        f = w.dir / p
        if f.is_file() and MARKER.search(f.read_text(errors="replace")):
            return None
    return await gitsafe.tree_of(w.git_dir, w.dir, tip)


async def integrate(slug: str, job_id: str, root_id: int, plan_mod) -> None:
    """Merge the done items' branches into agent/orch-<job6> and file the PR."""
    from . import agents_run, gitgate
    plan = plan_mod.load(slug)
    if not plan or not on(plan) or not plan.get("base"):
        return
    done = sorted((it for it in plan["items"] if it["status"] == "done" and (it.get("work") or {}).get("sha")),
                  key=lambda i: (i["work"].get("order", 0), i["id"]))
    if not done:
        return
    wj = plan["work_job"]
    gd = worktrees.resolve(slug).git_dir
    branch = worktrees.branch_for(worktrees.job_tag(wj), "merge")
    ref = f"refs/heads/{branch}"
    tip = plan["base"]["sha"]
    merge = {"branch": branch, "key": worktrees.work_key(slug, wj, "merge"), "sha": tip,
             "state": "merging", "files": 0, "request_id": None, "conflicts": []}
    results: dict[str, dict] = {}

    async def save(**kw):
        merge.update(kw)
        async with plan_mod.edit(slug) as p:
            p["merge"] = dict(merge)
            for iid, upd in results.items():
                cur = plan_mod.index(p).get(iid)
                if cur and cur.get("work"):
                    cur["work"].update(upd)

    def status(s: str) -> None:
        bus.publish(job_id, {"type": "node_status", "node_id": root_id, "status": s})

    status("merging")
    await save()
    included: list[dict] = []
    for it in done:
        sha = it["work"]["sha"]
        if await worktrees.is_ancestor(gd, sha, tip):
            results[it["id"]] = {"state": "merged"}
            included.append(it)
            continue
        if await worktrees.is_ancestor(gd, tip, sha):
            tip = sha
            results[it["id"]] = {"state": "merged"}
            included.append(it)
            continue
        tree, conflicts = await worktrees.merge_tree(gd, tip, sha)
        if not conflicts:
            tip = await worktrees.commit_tree(gd, tree, [tip, sha], f"cntrl: merge {it['title']}")
            results[it["id"]] = {"state": "merged"}
            included.append(it)
            continue
        merge["conflicts"] = conflicts
        await save(state="resolving")
        status("resolving")
        prev = included[-1]["title"] if included else "the earlier work"
        try:
            rtree = await _resolve_conflict(slug, plan, job_id, root_id, tip, tree, conflicts,
                                            it, prev, agents_run, plan_mod)
        except Exception:  # noqa: BLE001 — a failed resolver is a conflict left out
            rtree = None
        if rtree:
            tip = await worktrees.commit_tree(gd, rtree, [tip, sha],
                                              f"cntrl: merge {it['title']} (conflicts resolved)")
            results[it["id"]] = {"state": "resolved"}
            included.append(it)
            await save(state="merging")
        else:
            results[it["id"]] = {"state": "conflict", "conflict_with": conflicts}
            merge["state_note"] = "conflict"
            await save(state="merging")
    await worktrees.set_ref(gd, ref, tip)
    base = plan["base"]["sha"]
    nfiles = len(await worktrees.changed_files(gd, base, tip))
    left_out = [i for i, r in results.items() if r["state"] == "conflict"]
    merge.pop("state_note", None)
    await save(sha=tip, files=nfiles)
    parts = [{"item": it["id"], "title": it["title"], "branch": it["work"]["branch"],
              "sha": it["work"]["sha"], "files": it["work"].get("files", 0),
              "state": results.get(it["id"], {}).get("state", it["work"].get("state"))}
             for it in done]
    summary = (f"{plan['title']}: {len(included)} of {len(done)} done item(s) merged"
               + (f"; left out (conflict): {', '.join(left_out)}" if left_out else ""))
    req = await gitgate.create_merge_request(slug, job_id=wj, branch=branch, base_sha=base,
                                             parts=parts, summary=summary,
                                             conversation_id=root_id)
    final = "conflict" if left_out else "filed"
    await save(state=final, request_id=(req or {}).get("id"))
    if left_out and (req or {}).get("id"):
        pass     # the PR is filed without them; plan["merge"].state says conflict
    status("filed")


@contextlib.asynccontextmanager
async def nothing():
    yield
