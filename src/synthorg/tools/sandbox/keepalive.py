# module-kind: code
"""What a reused sandbox container runs as its main process.

Passing a command REPLACES the image's CMD, and for the sandbox image that CMD
is the only thing that starts the health server its own HEALTHCHECK dials. So a
keep-alive that merely holds the container open disables the health check while
leaving the probe itself correct and fully tested: the container reports
unhealthy for its whole life and nothing says why (FailingStreak 107, measured
on a live merge sandbox).

Its own module because two facts have to agree and neither belongs to the exec
plumbing: what the image installs, and what the container runs.
"""

from typing import Final

#: Where the sandbox image installs its health server. Must match the COPY
#: destination in ``docker/sandbox/Dockerfile``, which the test asserts.
HEALTH_SERVER_PATH: Final[str] = "/usr/local/bin/healthz.py"

#: The container's main process. Guarded on the file existing so an
#: ``image_override`` devcontainer, which carries no health server, keeps
#: working and simply reports no health; ``exec`` keeps this shell's replacement
#: the process holding the container open.
KEEPALIVE_COMMAND: Final[str] = "sh"
KEEPALIVE_ARGS: Final[tuple[str, ...]] = (
    "-c",
    (
        f"[ -f {HEALTH_SERVER_PATH} ] && python3 {HEALTH_SERVER_PATH} & "
        "exec tail -f /dev/null"
    ),
)

__all__ = ["HEALTH_SERVER_PATH", "KEEPALIVE_ARGS", "KEEPALIVE_COMMAND"]
