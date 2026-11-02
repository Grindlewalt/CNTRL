"""nov2: branch/state/files on agent-tree item nodes, the PR on the head node."""
from backend import agenttree


def test_branch_and_pr_fields_ride_the_nodes():
    plans = agenttree.Plans()
    plans.items[7] = {"id": "i1", "work": {"branch": "agent/7f3a2c-i1", "state": "done",
                                           "files": 4}}
    plans.heads[3] = {"merge": {"state": "filed", "request_id": 9, "files": 11,
                                "branch": "agent/orch-7f3a2c"}}
    rows = [{"id": 3, "kind": "head", "parent_id": None, "summary": "", "mode": None},
            {"id": 7, "kind": "subagent", "parent_id": 3, "summary": "[item i1] x"}]
    agenttree.decorate(rows, set(), {}, plans)
    assert rows[1]["branch"] == "agent/7f3a2c-i1" and rows[1]["work_state"] == "done"
    assert rows[1]["files"] == 4
    assert rows[0]["pr"]["request_id"] == 9 and rows[0]["pr"]["files"] == 11
