"""Shared fixtures.

Tests build their own agent database rather than reading the seeded one: a
security test that silently passes because `data/` was missing is worse than no
test at all.
"""

import sqlite3
from pathlib import Path

import pytest

from texting_agent.config import settings
from texting_agent.database import agent_db

ROWS = [
    ("ACC_1", "C001", "2024-01-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
    ("ACC_1", "C002", "2024-02-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
    ("ACC_2", "C900", "2024-03-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
]


@pytest.fixture
def agent_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "customer_agent.db"
    agent_db.create(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO customer_agent_records "
            "(account_id, customer_id, registration_date, data_as_of) "
            "VALUES (?, ?, ?, ?)",
            ROWS,
        )
    return path


@pytest.fixture
def agent_conn(agent_db_path: Path):
    conn = agent_db.connect(agent_db_path)
    yield conn
    conn.close()


@pytest.fixture
def app_db_path(tmp_path: Path) -> Path:
    return tmp_path / "app.db"


@pytest.fixture
def isolated_settings(monkeypatch, agent_db_path: Path, app_db_path: Path):
    """Point the running service at throwaway databases."""
    monkeypatch.setattr(settings, "agent_db_path", str(agent_db_path))
    monkeypatch.setattr(settings, "app_db_path", str(app_db_path))
    return settings
