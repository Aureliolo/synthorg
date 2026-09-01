# module-kind: code
"""What a reused sandbox container runs as its main process.

Extracted rather than left inline: passing a command REPLACES the image's CMD,
and for the sandbox image that CMD is the only thing that starts the health
server the image's own HEALTHCHECK dials. A plain ``tail -f /dev/null`` kept the
container alive and silently disabled its health check, so every container
reported unhealthy for its whole life (FailingStreak 107, measured on a live
merge sandbox) while the probe itself was correct and fully tested.

Its own module because the two facts have to agree and neither belongs to the
exec plumbing: what the image installs, and what the container runs.
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
