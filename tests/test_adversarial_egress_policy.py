"""Adversarial audit of backend/egress.py (egress policy; approvals per profile since 2026-11-14).

test_GAP_* assert secure expectations and are EXPECTED TO FAIL; test_CONTROL_*
confirm a control that holds. Run:
.venv/bin/python -m pytest tests/test_adversarial_egress_policy.py -q
"""
import pytest

from backend import db as db_mod, egress, profiles


@pytest.fixture
async def db(tmp_env):
    await db_mod.init_db()
    egress._cut.clear()
    conn = await db_mod.get_db()
    yield conn
    await conn.close()


# --- CONTRACT (operator's decision 2026-11-14): an approval writes the PROFILE's
# list. Every project on that profile shares it, so approving a site for one
# project lets every project on the same profile reach it (Auto review's
# approvals too). The containment boundary is the profile: a project on another
# profile is never widened. (2026-09-26 to 2026-11-14 an approval wrote the
# project's own list; before that a default project's approval trained GENERAL.)

async def _on_own_profile(db, slug, name):
    await db.execute("INSERT INTO projects(slug, name, path) VALUES (?, ?, ?)",
                     (slug, slug, f"/tmp/{slug}"))
    await db.commit()
    prof = await profiles.create(db, {"name": name, "service_placement": "per_project",
                                      "box_runtime": "kvm"})
    await profiles.assign(db, slug, prof["id"])
    return prof


async def test_an_approval_widens_its_profile_and_no_other(db):
    finance = await _on_own_profile(db, "finance", "Finance")
    await egress.note_denied(db, "projA", "shared-cdn.com")
    pid = (await egress.list_pending(db, "projA"))[0]["id"]
    res = await egress.approve_host(db, pid)
    default = await profiles.for_slug(db, "projA")
    assert res["list"] == f"profile:{default['id']}"   # the profile's list, named
    assert (await egress.decide(db, "projA", "shared-cdn.com"))[0] == "allow"
    # the documented consequence: the same profile shares it
    assert (await egress.decide(db, "projB", "shared-cdn.com"))[0] == "allow"
    # the boundary: another profile is not widened
    assert finance["id"] != default["id"]
    assert (await egress.decide(db, "finance", "shared-cdn.com"))[0] == "deny"


async def test_scoped_project_is_isolated_from_general_training(db):
    # a sensitive project with its OWN non-inheriting allowlist is unaffected by
    # any default project's approvals.
    await egress.set_policy(db, "finance", mode="allowlist",
                            inherit_general=False, hosts=["internal.api"])
    await egress.note_denied(db, "projA", "exfil.attacker.xyz")
    pid = (await egress.list_pending(db, "projA"))[0]["id"]
    await egress.approve_host(db, pid)
    assert (await egress.decide(db, "finance", "exfil.attacker.xyz"))[0] == "deny"


async def test_an_approval_on_a_custom_profile_stays_on_that_profile(db):
    await _on_own_profile(db, "scoped", "Scoped")
    await egress.note_denied(db, "scoped", "b.com")
    pid = (await egress.list_pending(db, "scoped"))[0]["id"]
    res = await egress.approve_host(db, pid)
    scoped = await profiles.for_slug(db, "scoped")
    assert res["list"] == f"profile:{scoped['id']}" and res["profile"] == "Scoped"
    assert (await egress.decide(db, "scoped", "b.com"))[0] == "allow"
    assert (await egress.decide(db, "other-default", "b.com"))[0] == "deny"


# --- CONTROL: host-matching does not over-permit lookalikes -------------------
@pytest.mark.parametrize("host", [
    "pypi.org.evil.com",      # suffix trick
    "evilpypi.org",           # no dot boundary
    "pythonhosted.org.evil",  # substring, wrong boundary
])
async def test_CONTROL_lookalike_hosts_denied(db, host):
    assert (await egress.decide(db, "p", host))[0] == "deny"


@pytest.mark.parametrize("host", ["PYPI.ORG", "pypi.org.", "x.pypi.org"])
async def test_CONTROL_legit_case_dot_and_subdomain_allowed(db, host):
    # case-fold, trailing dot, and real subdomains resolve to the same allowed host
    assert (await egress.decide(db, "p", host))[0] == "allow"


# --- CONTROL: cut takes precedence over every mode ----------------------------
async def test_CONTROL_cut_beats_denyall_and_denylist(db):
    await egress.set_policy(db, "sp", mode="denyall")
    egress.mark_cut("sp", "pypi.org")
    assert (await egress.decide(db, "sp", "pypi.org"))[0] == "cut"
