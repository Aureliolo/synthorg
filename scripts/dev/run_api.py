"""Local dev entrypoint for the SynthOrg API on Windows.

uvicorn defaults to the ProactorEventLoop on Windows, which psycopg's async
pool cannot drive. Run uvicorn's server on an explicit SelectorEventLoop
instead (the loop the psycopg error itself recommends; matches how the test
suite pins Selector for unit tiers). Subprocess-spawning features (sandbox /
git) are not exercised by the dashboard flows this dev loop iterates on.

Host/port are fixed at 127.0.0.1:3001 to match ``backend_dev.mjs`` and the
Vite dev-server proxy target. Launched by ``backend_dev.mjs`` with the
container's env overlaid; not a production entrypoint.
"""

import asyncio
import selectors
import sys

import uvicorn

if __name__ == "__main__":
    config = uvicorn.Config(
        "synthorg.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=3001,
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
