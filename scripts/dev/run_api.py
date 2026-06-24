"""Local dev entrypoint for the SynthOrg API on Windows.

uvicorn defaults to the ProactorEventLoop on Windows, which psycopg's async
pool cannot drive. Run uvicorn's server on an explicit SelectorEventLoop
instead (the loop the psycopg error itself recommends; matches how the test
suite pins Selector for unit tiers). Subprocess-spawning features (sandbox /
git) are not exercised by the dashboard flows this dev loop iterates on.

Host/port come from ``UVICORN_HOST`` / ``UVICORN_PORT`` (set by
``backend_dev.mjs``), defaulting to 127.0.0.1:3001 to match the Vite dev-server
proxy target. Launched by ``backend_dev.mjs`` with the container's env overlaid;
not a production entrypoint.
"""

import asyncio
import os
import selectors
import sys

import uvicorn

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 3001

if __name__ == "__main__":
    config = uvicorn.Config(
        "synthorg.api.app:create_app",
        factory=True,
        host=os.environ.get("UVICORN_HOST", _DEFAULT_HOST),
        port=int(os.environ.get("UVICORN_PORT", str(_DEFAULT_PORT))),
        loop="asyncio",
        access_log=False,
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
    else:
        asyncio.run(server.serve())
