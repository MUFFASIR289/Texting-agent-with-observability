"""Read-only access to the agent's single-table database.

This module is the only way to reach customer data. Three independent layers must
all fail before a write could happen:

1. Physical  - the file it opens contains exactly one table, and no second file
   can be attached to the connection (SEC-02).
2. Connection - `mode=ro` makes the driver reject writes; `query_only` rejects them
   again inside the SQL engine (SEC-01).
3. Interface  - callers get semantic repository methods, never SQL (SEC-03).

SQLite has no roles and no GRANT, so this is what "the database enforces it" means
here. The same guarantee becomes a native `GRANT SELECT` role on PostgreSQL.
"""

import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema_agent.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the agent database read-only. Raises if the file does not exist."""
    path = Path(path)
    # mode=ro requires an existing file; sqlite would otherwise silently create one.
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA trusted_schema = OFF")
    # mode=ro stops writes but not reads of a *second* file: without this, ATTACH
    # would let one statement see app state. Zero attached databases allowed.
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    return conn


def create(path: str | Path) -> None:
    """Create an empty agent database. Used by the seed script only.

    Deliberately separate from `connect`: nothing that serves a request may ever
    hold a writable handle to this file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def is_read_only(conn: sqlite3.Connection) -> bool:
    """Probe the connection by attempting a write. Used by /health."""
    try:
        conn.execute("CREATE TABLE _probe (x INTEGER)")
    except sqlite3.DatabaseError:
        return True
    conn.execute("DROP TABLE IF EXISTS _probe")
    return False


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
