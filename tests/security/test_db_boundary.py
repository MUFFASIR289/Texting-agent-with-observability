"""SEC-01, SEC-02: the agent's connection cannot write, and can see one table.

These assert the mechanism, not the intent. A system prompt asking the model not
to modify data would pass no test here.
"""

import sqlite3

import pytest

from texting_agent.database import agent_db

WRITES = [
    "INSERT INTO customer_agent_records (account_id, customer_id, registration_date, data_as_of) "
    "VALUES ('X', 'Y', '2024-01-01', '2024-01-01')",
    "UPDATE customer_agent_records SET total_spend = 0",
    "DELETE FROM customer_agent_records",
    "DROP TABLE customer_agent_records",
    "ALTER TABLE customer_agent_records ADD COLUMN sneaky TEXT",
    "CREATE TABLE orders (id INTEGER)",
    "CREATE INDEX idx_sneaky ON customer_agent_records (email)",
    "REPLACE INTO customer_agent_records (account_id, customer_id, registration_date, data_as_of) "
    "VALUES ('X', 'Y', '2024-01-01', '2024-01-01')",
    "UPDATE sqlite_master SET sql = 'x'",
]


@pytest.mark.parametrize("statement", WRITES, ids=lambda s: s.split()[0] + "-" + s.split()[1])
def test_every_write_is_rejected(agent_conn, statement):
    with pytest.raises(sqlite3.DatabaseError):
        agent_conn.execute(statement)


def test_reads_still_work(agent_conn):
    assert agent_conn.execute("SELECT COUNT(*) FROM customer_agent_records").fetchone()[0] == 3


def test_attaching_another_database_is_rejected(agent_conn, tmp_path):
    other = tmp_path / "app.db"
    sqlite3.connect(other).close()
    with pytest.raises(sqlite3.DatabaseError):
        agent_conn.execute(f"ATTACH DATABASE '{other.as_posix()}' AS app")


def test_writable_schema_does_not_unlock_the_schema(agent_conn):
    agent_conn.execute("PRAGMA writable_schema = ON")   # accepted, but inert
    with pytest.raises(sqlite3.DatabaseError):
        agent_conn.execute("UPDATE sqlite_master SET sql = 'x'")


def test_query_only_cannot_be_switched_off(agent_conn):
    agent_conn.execute("PRAGMA query_only = OFF")
    with pytest.raises(sqlite3.DatabaseError):
        agent_conn.execute("DELETE FROM customer_agent_records")


def test_database_contains_exactly_one_table(agent_conn):
    assert agent_db.table_names(agent_conn) == ["customer_agent_records"]


def test_no_view_or_trigger_can_smuggle_in_another_source(agent_conn):
    rows = agent_conn.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('view', 'trigger')"
    ).fetchall()
    assert rows == []


def test_connection_reports_itself_read_only(agent_conn):
    assert agent_db.is_read_only(agent_conn) is True


def test_connecting_to_a_missing_file_fails_rather_than_creating_one(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        agent_db.connect(missing)
    assert not missing.exists()
