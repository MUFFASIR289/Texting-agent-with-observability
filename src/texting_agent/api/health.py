import sqlite3

from fastapi import APIRouter, Request

from texting_agent import __version__
from texting_agent.config import settings
from texting_agent.database import agent_db, app_db

router = APIRouter()

EXPECTED_AGENT_TABLES = ["customer_agent_records"]


def _agent_db_status() -> dict:
    """Prove the boundary on every check rather than trusting it was set up right."""
    try:
        conn = agent_db.connect(settings.agent_db_path)
    except sqlite3.DatabaseError as exc:
        return {"reachable": False, "error": type(exc).__name__}
    try:
        return {
            "reachable": True,
            "read_only": agent_db.is_read_only(conn),
            "tables": agent_db.table_names(conn),
        }
    finally:
        conn.close()


def _app_db_status() -> dict:
    try:
        conn = app_db.connect(settings.app_db_path)
    except sqlite3.DatabaseError as exc:
        return {"reachable": False, "error": type(exc).__name__}
    try:
        return {"reachable": True, "writable": app_db.is_writable(conn)}
    finally:
        conn.close()


@router.get("/")
def index(request: Request) -> dict:
    """An index, so the root path is an answer rather than a 404.

    The endpoint list is derived from the OpenAPI schema rather than written
    out here, so a route added later appears without anyone remembering to.
    """
    return {
        "service": "texting-agent",
        "version": __version__,
        "docs": "/docs",
        "endpoints": sorted(
            f"{method.upper()} {path}"
            for path, operations in request.app.openapi()["paths"].items()
            for method in operations
        ),
    }


@router.get("/health")
def health() -> dict:
    agent = _agent_db_status()
    app_state = _app_db_status()
    boundary_intact = (
        agent.get("read_only") is True
        and agent.get("tables") == EXPECTED_AGENT_TABLES
    )
    return {
        "status": "ok" if boundary_intact and app_state.get("writable") else "degraded",
        "service": "texting-agent",
        "version": __version__,
        "env": settings.env,
        "config_valid": True,  # settings parsed at import, so reaching here proves it
        "agent_db": agent,
        "app_db": app_state,
        "boundary_intact": boundary_intact,
    }
