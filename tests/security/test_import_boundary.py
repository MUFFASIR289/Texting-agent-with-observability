"""SEC-09: the agent package cannot reach app state or the outside world.

Written before `texting_agent/agent/` exists so that it fails the first time a
module there imports something it should not, rather than being remembered later.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "texting_agent"
AGENT = SRC / "agent"

# Writable state, delivery, and anything that would let the agent act on its own.
FORBIDDEN = (
    "texting_agent.database.app_db",
    "texting_agent.providers",
    "texting_agent.api",
    "sqlite3",
    "requests",
    "httpx",
)

AGENT_FILES = sorted(AGENT.rglob("*.py")) if AGENT.exists() else []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.skipif(not AGENT_FILES, reason="agent package does not exist yet (M5)")
@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.name)
def test_agent_modules_import_nothing_forbidden(path: Path):
    imported = _imported_modules(path)
    banned = {
        name for name in imported
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN)
    }
    assert banned == set(), f"{path.name} imports {banned}"


def test_the_agent_package_is_still_absent_or_scanned():
    """Fails loudly if the package appears but the glob above stops finding it."""
    assert AGENT_FILES or not AGENT.exists()
