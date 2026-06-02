"""Container log collection, parsing, and shipping utilities.

Extracted from ``docker_sandbox.py`` to keep each module under 800
lines.  All functions are free-standing so they can be tested
independently of ``DockerSandbox``.
"""

import asyncio
import json
from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_LOGS_COLLECTED,
    SANDBOX_CONTAINER_LOGS_MALFORMED,
    SANDBOX_CONTAINER_LOGS_SHIP_FAILED,
    SANDBOX_CONTAINER_LOGS_SHIPPED,
)

if TYPE_CHECKING:
    import aiodocker

    from synthorg.observability.config import ContainerLogShippingConfig

logger = get_logger(__name__)

# Maximum number of lines fetched from a sidecar container.  Bounds
# memory usage before the per-byte cap in ``parse_json_log_lines``
# applies.
_SIDECAR_LOG_TAIL_LIMIT: int = 10_000


def build_correlation_env() -> list[str]:
    """Build ``SYNTHORG_*`` env vars from structlog contextvars.

    Reads ``agent_id``, ``session_id``, ``task_id``, and
    ``request_id`` from the current structlog context and returns
    Docker env list entries.  Missing keys default to empty strings.

    Returns:
        List of ``KEY=value`` strings for container env injection.
    """
    import structlog.contextvars  # noqa: PLC0415

    ctx = structlog.contextvars.get_contextvars()
    return [
        f"SYNTHORG_AGENT_ID={ctx.get('agent_id', '')}",
        f"SYNTHORG_SESSION_ID={ctx.get('session_id', '')}",
        f"SYNTHORG_TASK_ID={ctx.get('task_id', '')}",
        f"SYNTHORG_REQUEST_ID={ctx.get('request_id', '')}",
    ]


def parse_json_log_lines(
    raw_lines: list[str],
    *,
    max_log_bytes: int,
    sidecar_id_short: str,
) -> tuple[dict[str, JsonValue], ...]:
    """Parse raw log lines as JSON, skipping malformed entries.

    Byte counting uses character length as a fast approximation for
    UTF-8 text.  Processing stops when the cumulative budget is
    exhausted.

    Args:
        raw_lines: Raw stdout lines from the sidecar container.
        max_log_bytes: Cumulative character-length cap.
        sidecar_id_short: Short sidecar ID for logging.

    Returns:
        Tuple of successfully parsed JSON dicts.
    """
    parsed: list[dict[str, JsonValue]] = []
    cumulative = 0
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        cumulative += len(stripped)
        if cumulative > max_log_bytes:
            break
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug(
                SANDBOX_CONTAINER_LOGS_MALFORMED,
                sidecar_id=sidecar_id_short,
            )
            continue
        if not isinstance(obj, dict):
            logger.debug(
                SANDBOX_CONTAINER_LOGS_MALFORMED,
                sidecar_id=sidecar_id_short,
                status="not_object",
            )
            continue
        parsed.append(obj)
    return tuple(parsed)


async def collect_sidecar_logs(
    docker: aiodocker.Docker,
    sidecar_id: str,
    *,
    config: ContainerLogShippingConfig,
) -> tuple[dict[str, JsonValue], ...]:
    """Collect and parse structured JSON logs from a sidecar.

    Reads sidecar stdout before container removal.  Each line is
    parsed as JSON; malformed lines are logged and skipped.
    Returns empty tuple if log retrieval itself fails (timeout,
    I/O error); partial results are returned when only some lines
    fail to parse.

    The ``tail`` parameter is used to cap the number of lines
    fetched from Docker, bounding memory usage before the per-byte
    cap in :func:`parse_json_log_lines` applies.

    Args:
        docker: Docker client.
        sidecar_id: Sidecar container ID.
        config: Log shipping configuration.

    Returns:
        Tuple of parsed JSON dicts.  Empty if collection fails.

    Raises:
        MemoryError: Propagated if raised during collection.
        RecursionError: Propagated if raised during collection.
    """
    try:
        container_obj = docker.containers.container(sidecar_id)  # pyright: ignore[reportAttributeAccessIssue]
        raw_lines: list[str] = await asyncio.wait_for(
            container_obj.log(
                stdout=True,
                stderr=False,
                tail=_SIDECAR_LOG_TAIL_LIMIT,
            ),
            timeout=config.collection_timeout_seconds,
        )
    except TimeoutError as exc:
        # WARNING (matched to the broader collection-failure path
        # below): a timeout still leaves the operator with empty logs
        # they can't distinguish from "container had nothing to say",
        # so the structured ``error_type`` / ``error`` fields make the
        # cause grep-able.
        logger.warning(
            SANDBOX_CONTAINER_LOGS_COLLECTED,
            sidecar_id=sidecar_id[:12],
            status="timeout",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    except Exception as exc:
        reraise_critical(exc)
        # WARNING (not DEBUG) so a Docker-API failure during sidecar log
        # collection surfaces in operator dashboards. Returning ``()``
        # yields an empty log set indistinguishable from "container
        # produced no output", which masks the failure unless the
        # severity is high enough to alert on.
        logger.warning(
            SANDBOX_CONTAINER_LOGS_COLLECTED,
            sidecar_id=sidecar_id[:12],
            status="error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()

    parsed = parse_json_log_lines(
        raw_lines,
        max_log_bytes=config.max_log_bytes,
        sidecar_id_short=sidecar_id[:12],
    )
    logger.debug(
        SANDBOX_CONTAINER_LOGS_COLLECTED,
        sidecar_id=sidecar_id[:12],
        status="ok",
        log_count=len(parsed),
    )
    return parsed


async def ship_container_logs(  # noqa: PLR0913
    *,
    config: ContainerLogShippingConfig,
    container_id: str,
    sidecar_id: str | None,
    stdout: str,
    stderr: str,
    sidecar_logs: tuple[dict[str, JsonValue], ...],
    execution_time_ms: int,
) -> None:
    """Ship container logs through the structlog pipeline.

    Failure-tolerant: shipping errors are logged at WARNING level and
    never propagated (except ``MemoryError`` and ``RecursionError``
    which always propagate). WARNING is intentional -- a shipping
    failure means agent / task logs are absent from the operator's
    pipeline, which is silent data loss unless the symptom ("no logs
    for this run") is paired with an audible cause.

    When ``config.ship_raw_logs`` is ``False`` (default), only
    metadata is shipped -- no raw stdout/stderr/sidecar payloads.
    This prevents secrets in container output from bypassing the
    key-name-based redaction layer.

    The ``max_log_bytes`` budget is enforced across stdout, stderr,
    and sidecar log entries combined.  Stdout is allocated first,
    then stderr from the remainder, then sidecar entries from
    whatever budget is left (each entry estimated via ``str()``).

    Args:
        config: Log shipping configuration.
        container_id: Sandbox container ID.
        sidecar_id: Sidecar container ID (may be None).
        stdout: Sandbox stdout output.
        stderr: Sandbox stderr output.
        sidecar_logs: Parsed sidecar log entries.
        execution_time_ms: Execution time in milliseconds.

    Raises:
        MemoryError: Propagated unconditionally.
        RecursionError: Propagated unconditionally.
    """
    if not config.enabled:
        return
    try:
        event_kwargs: dict[str, object] = {
            "container_id": container_id[:12],
            "sidecar_id": sidecar_id[:12] if sidecar_id else None,
            "stdout_size": len(stdout),
            "stderr_size": len(stderr),
            "sidecar_log_count": len(sidecar_logs),
            "execution_time_ms": execution_time_ms,
        }
        if config.ship_raw_logs:
            budget = config.max_log_bytes
            stdout_trunc = stdout[:budget]
            budget -= len(stdout_trunc)
            stderr_trunc = stderr[: max(budget, 0)]
            budget -= len(stderr_trunc)
            event_kwargs["stdout"] = stdout_trunc
            event_kwargs["stderr"] = stderr_trunc
            # Include sidecar entries that fit within remaining budget.
            if budget > 0 and sidecar_logs:
                included: list[dict[str, JsonValue]] = []
                for entry in sidecar_logs:
                    entry_est = len(str(entry))
                    if entry_est > budget:
                        break
                    included.append(entry)
                    budget -= entry_est
                if included:
                    event_kwargs["sidecar_logs"] = tuple(included)
        logger.info(SANDBOX_CONTAINER_LOGS_SHIPPED, **event_kwargs)
    except Exception as exc:
        reraise_critical(exc)
        # WARNING (not DEBUG): a shipping failure means agent / task
        # logs are absent from the operator's pipeline, so the symptom
        # ("no logs for this run") needs to be paired with an audible
        # cause to triage.
        logger.warning(
            SANDBOX_CONTAINER_LOGS_SHIP_FAILED,
            container_id=container_id[:12],
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
