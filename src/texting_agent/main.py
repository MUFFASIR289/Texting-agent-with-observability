import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from texting_agent.api import health
from texting_agent.config import settings
from texting_agent.database import app_db
from texting_agent.observability.logging import configure_logging
from texting_agent.services import playbook_service, scoring_config

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    app_db.bootstrap(settings.app_db_path)
    # Validate YAML config here so a bad weight or a misspelled offer type is a
    # failed boot, not a bad campaign hours later.
    scoring = scoring_config.get()
    playbooks = playbook_service.get()
    log.info("service.start", env=settings.env, port=settings.port,
             scoring_config_version=scoring.version,
             playbook_count=len(playbooks.playbooks))
    yield
    log.info("service.stop")


app = FastAPI(title="Texting Agent", lifespan=lifespan)
app.include_router(health.router)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Detail goes to the logs; the client gets an id to quote."""
    request_id = str(uuid.uuid4())
    log.exception("request.failed", path=request.url.path, request_id=request_id)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "correlation_id": request_id}},
    )
