"""Read-write access to operational state.

Separate file from the agent database, and deliberately unreachable from
`texting_agent.agent.*` - an import-boundary test enforces that (SEC-09).
"""

import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema_app.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def bootstrap(path: str | Path) -> None:
    """Create tables if absent. Safe to call on every startup."""
    with connect(path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def is_writable(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("PRAGMA user_version").fetchone()
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.DatabaseError:
        return False
