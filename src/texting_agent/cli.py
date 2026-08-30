"""Entry point for `uv run texting-agent`."""

import uvicorn

from texting_agent.config import settings


def main() -> None:
    uvicorn.run(
        "texting_agent.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,  # structlog owns logging
    )
