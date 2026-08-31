"""Entry point for `uv run texting-agent`.

Starts the whole agent on one port. The API is Python and the UI is Node, so
there are two processes - but only one of them is reachable from outside the
loopback, and only one address is ever typed:

    http://127.0.0.1:8000/          the landing page
    http://127.0.0.1:8000/console   the operator console
    http://127.0.0.1:8000/api/...   the API, and /api/docs

The UI holds the public port and forwards /api to the API on the loopback. Set
SERVE_UI=false to run the API alone, on the public port, as it used to be.
"""

import atexit
import os
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn

from texting_agent.config import settings

WEB = Path(__file__).resolve().parents[2] / "web"


def _start_ui() -> subprocess.Popen | None:
    """Launch the built UI on the public port. Returns None if it cannot."""
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm was not found, and the UI needs it. Install Node, or set SERVE_UI=false.")
    if not (WEB / ".next").is_dir():
        sys.exit(f"The UI is not built yet. Run:\n\n    cd {WEB} && npm install && npm run build\n")

    process = subprocess.Popen(
        [npm, "run", "start", "--", "--port", str(settings.port), "--hostname", settings.host],
        cwd=WEB,
        env={**os.environ,
             # The UI reaches the API over the loopback, not through itself.
             "TEXTING_AGENT_URL": f"http://127.0.0.1:{settings.api_port}",
             "API_INTERNAL_PORT": str(settings.api_port)},
    )
    atexit.register(_stop, process)
    return process


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> None:
    if settings.serve_ui:
        _start_ui()
        # ASCII only: the default Windows console is cp1252, and an arrow
        # raises UnicodeEncodeError before the service ever starts.
        url = f"http://{settings.host}:{settings.port}"
        print(f"\n  Texting Agent   {url}\n  API docs        {url}/api/docs\n",
              flush=True)

    uvicorn.run(
        "texting_agent.main:app",
        host="127.0.0.1" if settings.serve_ui else settings.host,
        # Behind the UI the API stays on the loopback; alone, it takes the
        # public port so nothing about the old way of running it changes.
        port=settings.api_port if settings.serve_ui else settings.port,
        log_config=None,  # structlog owns logging
    )
