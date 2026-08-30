import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request

from texting_agent.api import errors, health
from texting_agent.config import settings
from texting_agent.database import app_db
from texting_agent.deps import authenticate
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
             playbook_count=len(playbooks.playbooks),
             api_keys_configured=len(settings.api_keys))
    yield
    log.info("service.stop")


def create_app() -> FastAPI:
    # Authentication is applied to the whole app rather than route by route, so
    # a route added later is protected without anyone remembering to say so.
    app = FastAPI(title="Texting Agent", lifespan=lifespan,
                  dependencies=[Depends(authenticate)])
    errors.register(app)
    app.include_router(health.router)

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        return response

    return app


app = create_app()
