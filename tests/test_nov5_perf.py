"""nov5 speed-ups that must not change answers (measured on the Pi, 2026-11-10):
the Memory badge re-parsed every note's YAML on every poll (~90 ms a call), so a
note's trust is remembered until the file changes, and notes parse with PyYAML's
C loader when it is there."""
import os

import yaml

from backend import memory

AGENT_NOTE = "---\nsource: agent\napproved: false\ncreated: 2026-11-10\ntags: [a, b]\n---\nbody\n"
APPROVED = "---\nsource: agent\napproved: true\ncreated: 2026-11-10\ntags: [a, b]\n---\nbody\n"


def test_the_badge_follows_a_note_that_changes(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    p = notes / "n.md"
    p.write_text(AGENT_NOTE)
    assert memory.pending_counts(notes)["notes"] == 1
    assert memory.pending_counts(notes)["notes"] == 1          # remembered, same answer
    p.write_text(APPROVED)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))   # a later write, surely
    assert memory.pending_counts(notes)["notes"] == 0
    p.unlink()
    assert memory.pending_counts(notes)["notes"] == 0


def test_the_fast_loader_reads_a_note_as_the_old_one_did():
    head = AGENT_NOTE.split("---")[1]
    meta, body = memory.parse_note(AGENT_NOTE)
    assert meta == yaml.safe_load(head) and body == "body"
    # a header that is not a mapping still fails closed
    assert memory.note_trusted(memory.parse_note("---\n- just a list\n---\nx")[0]) is False
