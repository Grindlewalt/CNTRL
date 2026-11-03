"""An alert acknowledged anywhere (a Queue card, a decided request, a bulk clear) is
announced on the security channel as `security_acked`, so a live client takes its
notice off (2026-11-03: the terminal's sidebar kept every notice, answered or not). A
decided package, service or always-loaded hold acknowledges its request's alert."""
import pytest

from backend import bus, security
from backend.db import get_db, init_db


@pytest.fixture
async def db(tmp_env):
    await init_db()
    d = await get_db()
    yield d
    await d.close()


def _drain(q) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return [e for e in out if e.get("type") == "security_acked"]


@pytest.fixture
def chan():
    q = bus.subscribe(security.SECURITY_CHAN)
    yield q
    bus.unsubscribe(security.SECURITY_CHAN, q)


async def _raise(db, kind, **detail):
    # a summary of its own: the same one would count onto the first row (coalescing)
    await security.raise_event(db, kind=kind, severity="warn", summary=f"{kind} {detail}",
                               detail=detail)
    async with db.execute("SELECT MAX(id) AS m FROM security_events") as cur:
        return (await cur.fetchone())["m"]


async def _open(db) -> set[int]:
    async with db.execute("SELECT id FROM security_events WHERE acknowledged = 0") as cur:
        return {r["id"] for r in await cur.fetchall()}


async def test_acknowledge_announces_the_id(db, chan):
    eid = await _raise(db, "unexpected_process", pid=1)
    _drain(chan)
    await security.acknowledge(db, eid)
    assert _drain(chan) == [{"type": "security_acked", "ids": [eid]}]


async def test_settle_announces_what_it_acknowledged_and_nothing_when_nothing(db, chan):
    a = await _raise(db, "package_requested", package_id=5)
    b = await _raise(db, "package_requested", package_id=6)
    _drain(chan)
    assert await security.settle(db, "package_requested", "package_id", 5) == 1
    assert _drain(chan) == [{"type": "security_acked", "ids": [a]}]
    assert await _open(db) == {b}
    assert await security.settle(db, "package_requested", "package_id", 5) == 0
    assert _drain(chan) == []


async def test_a_bulk_clear_announces_its_scope(db, chan):
    await _raise(db, "unexpected_process", pid=1)
    keep = await _raise(db, "harness_fault", what="x")
    _drain(chan)
    await security.acknowledge_all(db, exclude="harness_fault")
    assert _drain(chan) == [{"type": "security_acked", "all": True, "only": None,
                             "exclude": "harness_fault"}]
    assert await _open(db) == {keep}
    await security.acknowledge_all(db, exclude="harness_fault")      # nothing left to clear
    assert _drain(chan) == []


async def test_a_decided_package_request_leaves_the_queue(db):
    from tests.test_packages import _seed
    from backend import packages
    await _seed(db)
    row = await packages.file_request(db, manager="apt", package="cowsay", version=None,
                                      install_command="", reason="fun", project="c",
                                      conversation_id=None)
    other = await packages.file_request(db, manager="apt", package="sl", version=None,
                                        install_command="", reason="fun", project="c",
                                        conversation_id=None)
    async with db.execute("SELECT id, json_extract(detail, '$.package_id') AS p FROM "
                          "security_events WHERE kind = 'package_requested'") as cur:
        by_pkg = {r["p"]: r["id"] for r in await cur.fetchall()}
    await packages.reject(db, row["id"], reason="no")
    still = await _open(db)
    assert by_pkg[row["id"]] not in still and by_pkg[other["id"]] in still
    await packages.set_resolution(db, other["id"], resolved_version="5.0-1",
                                  integrity="sha256:x")
    await packages.approve(db, other["id"], target_variant=None)
    assert by_pkg[other["id"]] not in await _open(db)
