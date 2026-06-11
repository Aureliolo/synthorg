"""Minimal health check HTTP server for the sandbox container.

Serves GET /healthz on port 15003 with a JSON response. Runs as a
background process alongside the sandbox's main sleep loop. Uses only
stdlib -- no external dependencies.
"""

import http.server
import json
import time
from typing import override


class _Handler(http.server.BaseHTTPRequestHandler):
    _start_time: float = 0.0

    def do_GET(self) -> None:
        if self.path == "/healthz":
            body = json.dumps(
                {
                    "status": "healthy",
                    "uptime_seconds": int(time.monotonic() - self._start_time),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_error(404)

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass  # Suppress access logs.


if __name__ == "__main__":
    _Handler._start_time = time.monotonic()  # noqa: SLF001
    server = http.server.HTTPServer(("0.0.0.0", 15003), _Handler)  # noqa: S104
    server.serve_forever()
