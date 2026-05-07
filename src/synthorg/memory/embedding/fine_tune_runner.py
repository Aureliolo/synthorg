"""Fine-tune pipeline container entrypoint.

Reads stage configuration from ``/etc/fine-tune/config.json``, executes
the requested pipeline stage, and emits structured progress markers on
stdout for the orchestrator to parse.

Designed to run as ``python -m synthorg.memory.embedding.fine_tune_runner``
inside the ``synthorg-fine-tune-gpu`` (default) or ``synthorg-fine-tune-cpu``
container. Both ship the same Python entry point; they differ only in the
bundled torch build (CUDA vs CPU).

Uses ``print()`` for structured stdout/stderr markers that the
orchestrator parses from Docker container logs -- this is an entrypoint
script, not application library code.
"""

import asyncio
import http.server
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Final

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.observability.events.fine_tune import (
    FINE_TUNE_HEALTH_SERVER_BIND_FAILED,
    FINE_TUNE_HEALTH_SERVER_STARTED,
    FINE_TUNE_HEALTH_SERVER_STOPPED,
)

logger = get_logger(__name__)

_CONFIG_PATH = Path("/etc/fine-tune/config.json")

_DEFAULT_HEALTH_PORT: Final[int] = 15002
_HEALTH_PORT_ENV_VAR: Final[str] = "SYNTHORG_FINE_TUNE_HEALTH_PORT"
_MIN_TCP_PORT: Final[int] = 1
_MAX_TCP_PORT: Final[int] = 65535
_MAX_LOGGED_ENV_CHARS: Final[int] = 64


def _resolve_health_port() -> int:
    """Resolve the HTTP health server port from env or default.

    Reads ``SYNTHORG_FINE_TUNE_HEALTH_PORT``; falls back to
    :data:`_DEFAULT_HEALTH_PORT` when unset. A malformed or
    out-of-range value is a startup-time container config error;
    log ``CONFIG_VALIDATION_FAILED`` and raise :class:`ValueError` so
    the orchestrator sees a fast, loud failure instead of the
    container silently binding the wrong port.
    """
    raw = os.environ.get(_HEALTH_PORT_ENV_VAR)
    if raw is None:
        return _DEFAULT_HEALTH_PORT
    # Truncate untrusted env input before logging to cap log line size
    # against pathological values pasted into container configuration.
    safe_raw = raw[:_MAX_LOGGED_ENV_CHARS]
    try:
        port = int(raw)
    except ValueError as exc:
        logger.exception(
            CONFIG_VALIDATION_FAILED,
            env_var=_HEALTH_PORT_ENV_VAR,
            value=safe_raw,
            reason="not-an-integer",
        )
        msg = f"{_HEALTH_PORT_ENV_VAR}={safe_raw!r} is not a valid integer"
        raise ValueError(msg) from exc
    if not (_MIN_TCP_PORT <= port <= _MAX_TCP_PORT):
        logger.error(
            CONFIG_VALIDATION_FAILED,
            env_var=_HEALTH_PORT_ENV_VAR,
            value=safe_raw,
            reason="out-of-range",
            min_port=_MIN_TCP_PORT,
            max_port=_MAX_TCP_PORT,
        )
        msg = (
            f"{_HEALTH_PORT_ENV_VAR}={port} out of range "
            f"[{_MIN_TCP_PORT}, {_MAX_TCP_PORT}]"
        )
        raise ValueError(msg)
    return port


# Stage functions have different signatures; the runner dispatches by
# unpacking config JSON into kwargs per stage.  Typed as Any because
# mypy cannot narrow across the heterogeneous union.
_EXECUTABLE_STAGES: frozenset[FineTuneStage] = frozenset(
    {
        FineTuneStage.GENERATING_DATA,
        FineTuneStage.MINING_NEGATIVES,
        FineTuneStage.TRAINING,
        FineTuneStage.EVALUATING,
        FineTuneStage.DEPLOYING,
    }
)


def _load_config() -> dict[str, Any] | None:
    """Load and validate the stage config JSON.

    Returns:
        Parsed config dict, or ``None`` on failure.
    """
    if not _CONFIG_PATH.exists():
        print(  # noqa: T201
            f"ERROR: config file not found at {_CONFIG_PATH}",
            file=sys.stderr,
        )
        return None

    try:
        raw = _CONFIG_PATH.read_text(encoding="utf-8")
        config = json.loads(raw)
    except OSError as exc:
        print(  # noqa: T201
            f"ERROR: unable to read config file {_CONFIG_PATH}: {safe_error_description(exc)}",  # noqa: E501
            file=sys.stderr,
        )
        return None
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid config JSON: {exc.msg}", file=sys.stderr)  # noqa: T201
        return None

    if not isinstance(config, dict):
        print("ERROR: config must be a JSON object", file=sys.stderr)  # noqa: T201
        return None
    return config


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Minimal health check handler for the fine-tune container."""

    _start_time: float = 0.0  # Set by _start_health_server before serving.

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

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Suppress access logs.


def _start_health_server() -> http.server.HTTPServer | None:
    """Start an HTTP health server on a daemon thread.

    Returns:
        The server instance, or ``None`` if the port is unavailable.
    """
    port = _resolve_health_port()
    try:
        server = http.server.HTTPServer(("0.0.0.0", port), _HealthHandler)  # noqa: S104
    except OSError:
        logger.warning(
            FINE_TUNE_HEALTH_SERVER_BIND_FAILED,
            port=port,
            reason="Health server could not bind; continuing without health endpoint",
        )
        return None
    _HealthHandler._start_time = time.monotonic()  # noqa: SLF001
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(FINE_TUNE_HEALTH_SERVER_STARTED, port=port)
    return server


def _shutdown_health_server(server: http.server.HTTPServer | None) -> None:
    """Shut down and close the health server if it was started."""
    if server is None:
        return
    port = server.server_port
    server.shutdown()
    server.server_close()
    logger.info(FINE_TUNE_HEALTH_SERVER_STOPPED, port=port)


def _run() -> int:
    """Execute the fine-tune stage and return an exit code."""
    health_server = _start_health_server()

    config = _load_config()
    if config is None:
        _shutdown_health_server(health_server)
        return 1

    stage_name = config.get("stage", "")

    try:
        stage = FineTuneStage(stage_name)
    except ValueError:
        print(f"ERROR: unknown stage {stage_name!r}", file=sys.stderr)  # noqa: T201
        _shutdown_health_server(health_server)
        return 1

    if stage not in _EXECUTABLE_STAGES:
        print(f"ERROR: stage {stage_name!r} is not executable", file=sys.stderr)  # noqa: T201
        _shutdown_health_server(health_server)
        return 1

    # Cooperative cancellation via SIGTERM (docker stop).
    token = CancellationToken()
    prev_handler = signal.signal(signal.SIGTERM, lambda *_: token.cancel())

    try:
        print(f"STAGE_START:{stage_name}", flush=True)  # noqa: T201
        try:
            asyncio.run(_dispatch_stage(stage, config, token))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            print(  # noqa: T201
                f"ERROR: {stage_name} failed: {safe_error_description(exc)}",
                file=sys.stderr,
            )
            return 1

        print(f"STAGE_COMPLETE:{stage_name}", flush=True)  # noqa: T201
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_handler)
        _shutdown_health_server(health_server)


async def _dispatch_stage(
    stage: FineTuneStage,
    config: dict[str, Any],
    token: CancellationToken,
) -> None:
    """Dispatch a stage call with the correct kwargs from config JSON.

    Args:
        stage: The pipeline stage to execute.
        config: Configuration dictionary with stage-specific keys.
        token: Cancellation token for SIGTERM handling.

    Raises:
        KeyError: If required config keys are missing for the stage.
    """
    # Lazy imports -- only load ML deps when actually running a stage.
    from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
        contrastive_fine_tune,
        deploy_checkpoint,
        evaluate_checkpoint,
        generate_training_data,
        mine_hard_negatives,
    )

    match stage:
        case FineTuneStage.GENERATING_DATA:
            await generate_training_data(
                source_dir=config["source_dir"],
                output_dir=config["output_dir"],
                cancellation=token,
            )
        case FineTuneStage.MINING_NEGATIVES:
            await mine_hard_negatives(
                training_data_path=config["training_data_path"],
                base_model=config["base_model"],
                output_dir=config["output_dir"],
                cancellation=token,
            )
        case FineTuneStage.TRAINING:
            await contrastive_fine_tune(
                training_data_path=config["training_data_path"],
                base_model=config["base_model"],
                output_dir=config["output_dir"],
                cancellation=token,
            )
        case FineTuneStage.EVALUATING:
            await evaluate_checkpoint(
                checkpoint_path=config["checkpoint_path"],
                base_model=config["base_model"],
                validation_data_path=config["validation_data_path"],
                output_dir=config["output_dir"],
                cancellation=token,
            )
        case FineTuneStage.DEPLOYING:
            await deploy_checkpoint(
                checkpoint_path=config["checkpoint_path"],
            )


if __name__ == "__main__":
    sys.exit(_run())
