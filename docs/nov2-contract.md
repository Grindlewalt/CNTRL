# nov2 contract: edit history, per-agent branches, the orchestrator's PR

Operator decisions (2026-11-02, don't re-ask):

- **Nothing an agent writes can be lost.** Every batch of agent writes the host applies
  becomes a commit on a hidden ref (the edit history). Box turns hand their edits home
  every ~60 s, not only at the end, so a box that dies loses a minute at most. A file
  or a whole turn can be put back.
- **Orchestrator jobs run on branches.** Each plan item works in its own copy of the
  project on its own branch. The project's files do not change until the operator
  approves the job's one PR. To try it first, `/pull` clones the PR branch.
- **Conflicts:** the orchestrator resolves them in the merge copy (same gates as any
  write); the operator approves or rejects the result. Nothing skips approval.
- **Sectioning is the operator's call per job:** before it plans, the orchestrator asks
  (ask_user) how to split the work into branches, with a recommended split first and
  "(Recommended)" at the end of its label; one option is always "no branches: write into
  the project as now".

Already on the branch (contract commit, tested in tests/test_gitsafe.py):

- `backend/gitsafe.py`: `git(git_dir, *args, work_tree=, index=, env=, check=, stdin=)`
  (hardened: explicit --git-dir/--work-tree, hooks/fsmonitor/system+global config off);
  `rev(git_dir, name)`; `tree_of(git_dir, work_tree, start)`;
  `snapshot(git_dir, work_tree, ref, message, *, start="HEAD", parents=()) -> sha|None`
  (throwaway index; honours .gitignore and info/exclude; harness files stay as the
  parent has them; CAS update-ref; None = nothing changed; HEAD/index/files untouched).
- `backend/worktrees.py`: work keys `<slug>--<job6>-<part>` (part `i<N>` or `merge`),
  `KEY_RE`, `job_tag`, `work_key`, `is_work_key`, `project_of`, `root()` =
  `settings.data_dir/worktrees`, `branch_for`, `resolve(key) -> Work(key, slug, dir,
  git_dir, branch, job, part)` (a plain slug resolves to the project itself, branch
  None), `HISTORY_REF = "refs/jav3/history"`. Stubs for B: `base_snapshot`, `create`,
  `remove`, `reap`.
- `backend/edithistory.py`: stub `after_apply(key, op_id, res)`; ALREADY CALLED at the
  end of `vm/workspace_xfer.apply_guest_writes` when anything was applied or deleted.
  Nobody else edits that call.
- `backend/gitgate.py`: stub `create_merge_request(slug, *, job_id, branch, base_sha,
  parts, summary, conversation_id=None) -> dict` (C implements, B calls).

## Names

| thing | name |
|---|---|
| a project's edit history | `refs/jav3/history` in the project's `.git` |
| a job's base | `refs/jav3/base/<job6>`: the project dir at job start (uncommitted work included) on top of HEAD; HEAD itself if clean |
| an item's copy | dir `data_dir/worktrees/<slug>--<job6>-i<N>`, branch `agent/<job6>-i<N>` |
| the merge copy / the PR branch | dir `.../<slug>--<job6>-merge`, branch `agent/orch-<job6>` (an `agent/*` name, so Gitea's `check_agent_ref` already allows it) |

`refs/jav3/*` is never advertised by `/git` (`uploadpack.hideRefs`) and never pushed.
`agent/*` branches are served by `/git` (that is how `/pull` gets the PR branch).

## The rule for a work key

Policy, settings, taint, the box, egress, secrets, DB rows, the project name shown to
the model: the PROJECT (`worktrees.project_of(key)` / `Work.slug`). Files on the host
and the guest's workspace dir: the KEY (`Work.dir`; the guest's
`projects_dir/<active_slug>` with `active_slug = key`). `runtime.active_project` stays
the project slug; a new contextvar `runtime.work_key` (None = the project itself)
carries the key for a child's turn.

## Plan data (B writes, C reads)

- `plan["isolation"]`: `"branches"` | `"shared"` (shared = today's behaviour). Set by the
  `orchestrate` tool's new optional `isolation` param (default `"branches"`).
- `plan["base"]`: `{"sha", "dirty": <files that were uncommitted at job start>}`.
- `item["work"]`: `{"key", "branch", "sha", "files": int, "state"}`; state `running |
  done | merged | conflict | resolved | failed`; `"conflict_with": [paths]` on conflict.
- `plan["merge"]`: `{"branch", "key", "sha", "state", "files": int, "request_id",
  "conflicts": [paths]}`; state `merging | resolving | filed | approved | rejected |
  conflict` (conflict = could not be resolved; the PR is filed without those items).

## The merge request (C)

`git_requests` row, kind `merge`, `message` = summary, new JSON column `payload` =
`{job_id, branch, base_sha, parts: [{item, title, branch, sha, files, state}]}`.

Approve (in `approve_request`, after the usual `gitea.reconcile` + `sync_main`):
1. M = main now; P = the branch tip. If M is not the base's first parent, merge M into
   the result with `git merge-tree --write-tree` (git >= 2.38; the Pi has 2.47); a
   conflict leaves the request pending with `error` naming the files.
2. Files changed in the project dir since the base (D = the dir's tree now vs base tree)
   that the PR also changes (C = base..P): if any, refuse with their names (stays
   pending). Paths only in D stay as they are, uncommitted.
3. main = merge commit (T, parents M and P), CAS on M. Then write T's version of every
   path in diff(base tree, T) into the project dir (deletions too), `git reset -q`
   (index = HEAD, files untouched), then the existing GitHub remote / `gitea.push_main`
   steps. Record the result in the edit history (`edithistory.after_apply(slug, None,
   {"applied": [...], "deleted": [...]})`).
4. Clean up: `worktrees.remove` every key of the job, drop `refs/jav3/base/<job6>`,
   delete the item branches (the merge commit keeps their commits reachable).

Reject: clean up the same way; the project is untouched.
With Gitea on: also push `agent/orch-<job6>` as the bot and open a Gitea PR with the
summary (read-only mirror); close it when the Jav3 request is decided.

## Who owns what

- **A: edit history + mid-turn checkpoints.** `backend/edithistory.py` (after_apply for
  slugs AND work keys: a work key commits onto `Work.branch` with start = the job base),
  `backend/history_api.py` (+ include in main.py): list entries, a diff, restore a file or
  a whole entry (operator only, `require_user`; a restore is itself recorded),
  `uploadpack.hideRefs=refs/jav3` for `/git` (git_serve_api.py), the 60 s mid-turn pull in
  `vm/guest_turn.py` (owner of the workspace only; `settings.turn_flush_seconds = 60`),
  the TUI's `/history [path]` and `/undo` (new commands only), docs/SELF.md section.
- **B: work keys end to end + the orchestrator.** `worktrees.py` stubs, `runtime.work_key`,
  threading the key through `vm/workspace_xfer.py` / `vm/guest_turn.py` /
  `orchestrator.job_workspace` / `flush_workspace` / `agents_run._run_headless` (and any
  host-side tool that writes into the project for a child), `plan.py` (per-item copies,
  the job's base, integration with merge-tree, the resolver item, filing
  `create_merge_request`), the `orchestrate` tool's `isolation`, `ORCHESTRATOR_PROMPT`
  (ask how to section first), reaper at startup.
- **C: the PR.** `gitgate.create_merge_request`, approve/reject for kind `merge`, the
  `payload` column (db.py migration), the Gitea mirror, `helper.pull_info` (`pr`: branch +
  clone/fetch commands), `agenttree` (branch/state/files on item nodes), the TUI (Queue
  rows and detail for a merge request, branch on agent rows, `/pull` showing the PR), the
  web app's queue (frontend/), docs/SELF.md "orchestrator PRs".

Everyone: tests for what you build; `pyflakes` on what you touch; never weaken a gate.
