# module-kind: code
"""Backend-aware dependency/GPU probing for the fine-tune preflight.

One ``ProbeResult`` feeds every preflight check regardless of execution
backend: the docker path boots an ephemeral probe container (TTL-cached
and coalesced so a polling dashboard cannot spawn containers per
request), the in-process path inspects the local torch install. The
sibling ``_preflight`` module consumes these to run the actual checks.
"""

import asyncio
from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.fine_tune_docker_runner import (
    FineTuneContainerRunner,
)
from synthorg.memory.embedding.fine_tune_models import FineTuneRequest
from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_BYTES_PER_GIB: Final[int] = 1024**3

# Probe results are cached briefly so an open fine-tune dashboard page
# polling preflight does not boot a probe container per request; short
# enough that pulling a fixed image or attaching a GPU shows up on the
# next poll. FAILED probes are cached for the same TTL deliberately: a
# broken daemon must not get hammered once per poll either.
_PROBE_TTL_SECONDS: Final[float] = 60.0
_probe_cache: dict[tuple[str, bool], tuple[float, ProbeResult]] = {}
# Concurrent cache misses for the same key coalesce onto one in-flight
# probe, so parallel requests can never spawn duplicate probe containers.
_probe_inflight: dict[tuple[str, bool], asyncio.Task[ProbeResult]] = {}

# End-to-end ceiling on the ephemeral-probe request path. Every inner
# Docker call is individually bounded (connect retries, create/start
# ceilings, the drain timeout), so this only converts a residual hang
# into a clean 503 instead of a wedged request.
_PROBE_REQUEST_CEILING_S: Final[float] = 300.0


async def _probe_and_cache(
    key: tuple[str, bool],
    clock: Clock,
) -> ProbeResult:
    """Run one container probe and record it in the TTL cache.

    Returns:
        Result of type ``ProbeResult``.
    """
    result = await FineTuneContainerRunner(clock=clock).probe(
        image=key[0],
        gpu_enabled=key[1],
    )
    _probe_cache[key] = (clock.monotonic(), result)
    return result


async def probe_fine_tune_image(
    *,
    image: str,
    gpu_enabled: bool,
    clock: Clock,
) -> ProbeResult:
    """Probe the fine-tune image via an ephemeral container, with a TTL cache.

    Returns:
        Result of type ``ProbeResult``.
    """
    key = (image, gpu_enabled)
    cached = _probe_cache.get(key)
    if cached is not None and clock.monotonic() - cached[0] < _PROBE_TTL_SECONDS:
        return cached[1]
    task = _probe_inflight.get(key)
    if task is None:
        # Awaited (shielded) just below; the task also outlives a
        # cancelled request so the probe result still lands in the
        # cache for the next poll.
        task = asyncio.create_task(_probe_and_cache(key, clock))
        task.add_done_callback(lambda _t: _probe_inflight.pop(key, None))
        _probe_inflight[key] = task
    return await asyncio.shield(task)


async def resolve_probe_target(
    request: FineTuneRequest,
    settings_service: SettingsService | None,
) -> tuple[str, bool]:
    """Derive which image (if any) the preflight probe should boot.

    Explicit docker execution wins (its image falling back to the boot
    cache); a request without an execution config derives from the
    cache plus the ``memory.fine_tune_default_gpu`` setting; explicit
    in-process execution means no container probe at all.

    Returns:
        ``(image, gpu_enabled)``; an empty image selects the local
        in-process torch inspection instead of a container probe.
    """
    from synthorg.memory.embedding.fine_tune_image_resolution import (  # noqa: PLC0415
        get_resolved_fine_tune_image,
    )

    execution = request.execution
    if execution is not None and execution.backend == "docker":
        image = execution.image or get_resolved_fine_tune_image()
        return image, execution.gpu_enabled
    if execution is None:
        image = get_resolved_fine_tune_image()
        return image, await resolve_probe_gpu_default(settings_service)
    return "", False


async def resolve_probe_gpu_default(
    settings_service: SettingsService | None,
) -> bool:
    """Resolve ``memory.fine_tune_default_gpu`` with an offline fallback.

    Returns:
        Result of type ``bool``.
    """
    if settings_service is None:
        return False
    try:
        entry = await settings_service.get("memory", "fine_tune_default_gpu")
    except SettingNotFoundError:
        return False
    return str(entry.value).strip().lower() == "true"


def local_probe() -> ProbeResult:
    """Inspect the local torch install (in-process execution backend).

    Returns:
        The same ``ProbeResult`` shape the container probe produces, so
        every downstream check is backend-agnostic.
    """
    from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
        verify_fine_tune_dependencies,
    )

    try:
        torch = verify_fine_tune_dependencies()
    except (ImportError, FineTuneDependencyError) as exc:
        # The GPU branch below logs its own degradation; without this, the
        # commoner failure by far is the one that leaves no trace at all.
        logger.warning(
            MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
            check="dependencies",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ProbeResult(ok=False, detail=safe_error_description(exc))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
            check="dependencies",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ProbeResult(
            ok=False,
            detail=f"dependency check failed: {safe_error_description(exc)}",
        )
    gpu: str | None = None
    vram_gb: float | None = None
    try:
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            gpu = str(props.name)
            vram_gb = props.total_memory / _BYTES_PER_GIB
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
            check="gpu",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ProbeResult(
            ok=True,
            detail="ML dependencies installed",
            gpu_error=f"GPU detection error: {safe_error_description(exc)}",
        )
    return ProbeResult(
        ok=True,
        gpu=gpu,
        vram_gb=vram_gb,
        detail="ML dependencies installed",
    )
