"""Every name an in-guest tool imports from toolctx exists in the GUEST's toolctx too
(2026-11-02: read_file/write_file/edit_file/list_files/crawl_codebase began importing
require_files, which only the host had, so in a box each failed to load)."""
import ast
from pathlib import Path

from backend.agent.tools.inguest import BOX_ONLY_TOOLS, IN_GUEST_TOOLS

ROOT = Path(__file__).resolve().parent.parent


def _imported_from_toolctx(handler: Path) -> set[str]:
    names = set()
    tree = ast.parse(handler.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("toolctx"):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "toolctx":
            names.add(node.attr)
    return names


def test_the_guest_toolctx_has_what_in_guest_tools_import():
    guest = ast.parse((ROOT / "guest/backend/agent/tools/toolctx.py").read_text())
    have = {n.name for n in guest.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                        ast.ClassDef))}
    missing = {}
    for tool in set(IN_GUEST_TOOLS) | set(BOX_ONLY_TOOLS):
        h = ROOT / "tools" / tool / "handler.py"
        if h.exists():
            lack = _imported_from_toolctx(h) - have
            if lack:
                missing[tool] = sorted(lack)
    assert not missing, missing
