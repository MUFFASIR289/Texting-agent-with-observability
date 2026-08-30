"""SEC-03: SQL lives in one place and is never assembled from values.

A grep would miss `"SELECT ... " + user_input`. This walks the AST instead: any
string that looks like SQL outside the sanctioned modules fails, and any f-string
that looks like SQL fails everywhere, including inside them.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "texting_agent"

# The only modules allowed to contain SQL text at all.
SQL_MODULES = {
    Path("database/agent_db.py"),
    Path("database/app_db.py"),
    Path("database/repositories/customer_repo.py"),
}

KEYWORDS = (
    "select ", "insert ", "update ", "delete ", "drop ", "alter ", "create ",
    "attach ", "pragma ", "replace into", " from ", " where ",
)

PY_FILES = sorted(SRC.rglob("*.py"))


def _looks_like_sql(text: str) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in KEYWORDS)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Prose that merely mentions SQL is documentation, not a query."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_there_are_python_files_to_scan():
    """Guards against the whole suite passing because the glob found nothing."""
    assert len(PY_FILES) > 5


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_sql_appears_only_in_the_sanctioned_modules(path: Path):
    relative = path.relative_to(SRC)
    if relative in SQL_MODULES:
        pytest.skip("sanctioned SQL module")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and _looks_like_sql(node.value)
    ]
    assert offenders == [], f"{relative} contains SQL: {offenders}"


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_no_sql_is_ever_built_by_interpolation(path: Path):
    """Applies to the sanctioned modules too - especially to them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _looks_like_sql(literal):
                offenders.append(literal)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"format", "format_map"}:
                target = node.func.value
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    if _looks_like_sql(target.value):
                        offenders.append(target.value)
    assert offenders == [], f"{path.relative_to(SRC)} interpolates SQL: {offenders}"
