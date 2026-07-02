# module-kind: code
"""Fine-tune preflight thresholds, checks, and batch-size recommendation.

Helper module for the memory fine-tune controller: resolves
operator-tunable thresholds, runs the (sync, thread-offloaded) document /
GPU / dependency / disk preflight checks, and recommends a batch size from
detected VRAM. Dependency and GPU state come from one ``ProbeResult``
(see the sibling ``_preflight_probe`` module) regardless of execution
backend. No Litestar surface; the controller imports these.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers.memory._preflight_probe import local_probe
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    PreflightCheck,
)
from synthorg.memory.embedding.fine_tune_probe_result import ProbeResult
from synthorg.memory.embedding.fine_tune_run_helpers import build_config
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
    MEMORY_FINE_TUNE_THRESHOLD_FALLBACK,
)
from synthorg.settings.definitions.memory_fine_tune import (
    FINE_TUNE_DEFAULT_BATCH_SIZE,
    FINE_TUNE_MIN_DOCS_RECOMMENDED,
    FINE_TUNE_MIN_DOCS_REQUIRED,
    FINE_TUNE_PREFLIGHT_MAX_DEPTH,
    FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


_BATCH_SIZE_BY_VRAM_GB: Final[tuple[tuple[float, int], ...]] = (
    (40.0, 128),
    (16.0, 64),
    (8.0, 32),
)


# Scheduling slack added on top of ``preflight_walk_timeout_s`` for the
# hard request ceiling. The in-thread monotonic deadline already bounds
# the walk once it starts running; this margin covers ``to_thread``
# pool scheduling, the parallel batch-size task, and result assembly so
# a saturated executor surfaces as a clean 503 instead of a hung
# request.
_PREFLIGHT_HARD_TIMEOUT_MARGIN_S: Final[float] = 5.0


class _FineTuneThresholds(BaseModel):
    """Fine-tune preflight thresholds resolved at request time.

    Settings registered under the ``memory.fine_tune_*`` keys are the
    operator-tuning surface for these values; the imported defaults
    serve only as fallbacks for boot-time / unit-test paths that do
    not have a ``SettingsService``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    default_batch_size: int = Field(ge=1)
    min_docs_required: int = Field(ge=1)
    min_docs_recommended: int = Field(ge=1)
    preflight_max_depth: int = Field(ge=1)
    preflight_walk_timeout_s: float = Field(gt=0.0)


async def _resolve_fine_tune_thresholds(
    settings_service: SettingsService | None,
) -> _FineTuneThresholds:
    """Resolve the three fine-tune preflight thresholds at request time.

    Falls back to the module-level ``FINE_TUNE_*`` constants for any
    setting that is missing from the registry, fails to parse as int,
    or when no ``SettingsService`` is available -- the controller
    must remain functional in offline / unit-test invocations.

    Returns:
        ``_FineTuneThresholds`` instance.
    """
    fallbacks = {
        "fine_tune_default_batch_size": FINE_TUNE_DEFAULT_BATCH_SIZE,
        "fine_tune_min_docs_required": FINE_TUNE_MIN_DOCS_REQUIRED,
        "fine_tune_min_docs_recommended": FINE_TUNE_MIN_DOCS_RECOMMENDED,
        "fine_tune_preflight_max_depth": FINE_TUNE_PREFLIGHT_MAX_DEPTH,
    }
    if settings_service is None:
        return _FineTuneThresholds(
            default_batch_size=fallbacks["fine_tune_default_batch_size"],
            min_docs_required=fallbacks["fine_tune_min_docs_required"],
            min_docs_recommended=fallbacks["fine_tune_min_docs_recommended"],
            preflight_max_depth=fallbacks["fine_tune_preflight_max_depth"],
            preflight_walk_timeout_s=FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S,
        )
    resolved: dict[str, int] = {}
    for key, fallback in fallbacks.items():
        try:
            entry = await settings_service.get("memory", key)
            value = int(entry.value)
        except (SettingNotFoundError, ValueError, TypeError) as exc:
            logger.debug(
                MEMORY_FINE_TUNE_THRESHOLD_FALLBACK,
                setting_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            resolved[key] = fallback
            continue
        # ``_FineTuneThresholds`` enforces ``ge=1`` on every int field
        # (the float walk-timeout is resolved separately below), so an
        # unparseable override (handled above) AND a non-positive one
        # ("0" / "-1") must both fall back rather than reach the
        # constructor and surface as a 500 from the controller.
        resolved[key] = value if value >= 1 else fallback
    # The walk timeout is a float and is resolved independently of the
    # int knobs above; the same fall-back-on-bad-input contract holds
    # (unparseable / non-positive -> imported default).
    try:
        timeout_entry = await settings_service.get(
            "memory",
            "fine_tune_preflight_walk_timeout_s",
        )
        timeout_value = float(timeout_entry.value)
    except (SettingNotFoundError, ValueError, TypeError) as exc:
        logger.debug(
            MEMORY_FINE_TUNE_THRESHOLD_FALLBACK,
            setting_key="fine_tune_preflight_walk_timeout_s",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        timeout_value = FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S
    if timeout_value <= 0.0:
        timeout_value = FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S
    # Cross-field invariant: ``min_docs_recommended >= min_docs_required``,
    # otherwise ``_check_documents`` could never emit the ``warn`` band
    # (a corpus passes the required floor but is still below recommended).
    # An operator that lowered ``recommended`` below ``required`` falls
    # back to the imported recommended default rather than constructing
    # an inconsistent threshold pair.
    if (
        resolved["fine_tune_min_docs_recommended"]
        < resolved["fine_tune_min_docs_required"]
    ):
        resolved["fine_tune_min_docs_recommended"] = max(
            FINE_TUNE_MIN_DOCS_RECOMMENDED,
            resolved["fine_tune_min_docs_required"],
        )
    return _FineTuneThresholds(
        default_batch_size=resolved["fine_tune_default_batch_size"],
        min_docs_required=resolved["fine_tune_min_docs_required"],
        min_docs_recommended=resolved["fine_tune_min_docs_recommended"],
        preflight_max_depth=resolved["fine_tune_preflight_max_depth"],
        preflight_walk_timeout_s=timeout_value,
    )


def _run_preflight_checks(  # noqa: PLR0913 -- flat tunable thresholds + injected probe
    request: FineTuneRequest,
    *,
    min_required: int = FINE_TUNE_MIN_DOCS_REQUIRED,
    min_recommended: int = FINE_TUNE_MIN_DOCS_RECOMMENDED,
    max_depth: int = FINE_TUNE_PREFLIGHT_MAX_DEPTH,
    walk_timeout_s: float = FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S,
    docker_probe: ProbeResult | None = None,
) -> tuple[list[PreflightCheck], ProbeResult]:
    """Run all pre-flight validation checks.

    Args:
        request: Fine-tune request containing source / output dirs.
        min_required: Hard floor on document count below which the
            preflight reports ``fail``. Resolved from the
            ``memory.fine_tune_min_docs_required`` setting at the API
            boundary; the imported default is used as the fallback for
            offline / unit-test invocations.
        min_recommended: Soft floor at or below which the preflight
            reports ``warn``. Resolved from the
            ``memory.fine_tune_min_docs_recommended`` setting under
            the same fallback contract as ``min_required``.
        max_depth: Directory recursion cap for the document scan.
        walk_timeout_s: Wall-clock deadline for the document scan.
        docker_probe: Ephemeral-container probe outcome for a
            docker-backed run, resolved (and cached) at the controller
            boundary; ``None`` means the in-process backend, whose
            local torch install is inspected here instead.

    Returns:
        The checks plus the effective probe (the batch-size
        recommendation derives from its VRAM reading).
    """
    probe = docker_probe if docker_probe is not None else local_probe()
    checks: list[PreflightCheck] = []
    checks.append(_check_dependencies(probe, containerised=docker_probe is not None))
    checks.append(_check_gpu(probe))
    # The document scan applies only to directory mode; trajectory mode
    # sources from persisted org history, so there is no source dir to walk.
    if request.source_dir is not None:
        checks.append(
            _check_documents(
                request.source_dir,
                min_required=min_required,
                min_recommended=min_recommended,
                max_depth=max_depth,
                walk_timeout_s=walk_timeout_s,
            )
        )
    # Disk space is checked against the directory the run will actually write
    # checkpoints to. ``build_config`` resolves the effective output dir
    # (request override, else the run default) so trajectory mode -- which has
    # no ``source_dir`` to fall back on -- is still covered.
    checks.append(_check_disk_space(build_config(request).output_dir))
    return checks, probe


def _check_documents(
    source_dir: str,
    *,
    min_required: int = FINE_TUNE_MIN_DOCS_REQUIRED,
    min_recommended: int = FINE_TUNE_MIN_DOCS_RECOMMENDED,
    max_depth: int = FINE_TUNE_PREFLIGHT_MAX_DEPTH,
    walk_timeout_s: float = FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S,
) -> PreflightCheck:
    """Check source directory has enough documents.

    The scan is bounded on two independent axes so a pathologically
    deep (symlink-loop / generated) or pathologically wide tree on a
    slow / stale-handle mount cannot turn this preflight endpoint into
    an unbounded filesystem traversal: ``max_depth`` caps recursion
    depth and ``walk_timeout_s`` is a wall-clock deadline. Hitting
    either bound returns a ``warn`` band (never a hang and never a
    false ``fail``): the operator is told the scan was truncated and
    can re-run against a shallower tree or raise the limits.

    Returns:
        ``PreflightCheck`` instance.
    """
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    src = Path(source_dir)
    if not src.exists():
        return PreflightCheck(
            name="documents",
            status="fail",
            message="Source directory not found",
        )
    exts = (".txt", ".md", ".rst")
    # Sync helper run in an ``asyncio.to_thread`` worker for the
    # ``os.walk`` sweep; an async Clock seam cannot be awaited here, so
    # this monotonic deadline is a genuine elapsed-time primitive.
    # lint-allow: clock-seam -- sync to_thread os.walk deadline
    deadline = time.monotonic() + walk_timeout_s
    count = 0
    truncated = False
    # ``os.walk`` is a generator, so this is a ``for`` (not ``while``)
    # loop: the long-running-loop kill-switch gate only inspects
    # ``while`` loops, and this sweep is bounded by both the depth
    # prune and the monotonic deadline regardless. ``followlinks``
    # stays False so a symlink cycle cannot defeat the depth cap.
    for root, dirnames, filenames in os.walk(src, followlinks=False):
        # lint-allow: clock-seam -- sync to_thread os.walk deadline
        if time.monotonic() >= deadline:
            truncated = True
            break
        depth = len(Path(root).relative_to(src).parts)
        count += sum(1 for f in filenames if f.endswith(exts))
        if depth >= max_depth:
            if dirnames:
                # Sub-directories exist below the cap and will NOT be
                # scanned: surface that as a truncation warn rather
                # than silently under-counting.
                truncated = True
            # Prune deeper traversal in place; os.walk honours this.
            dirnames[:] = []
    if truncated:
        return PreflightCheck(
            name="documents",
            status="warn",
            message=(
                f"Document scan truncated after {walk_timeout_s:g}s "
                f"(depth cap {max_depth}); counted {count}+ so far. "
                "Re-run against a shallower source tree or raise "
                "memory.fine_tune_preflight_* limits."
            ),
        )
    if count < min_required:
        return PreflightCheck(
            name="documents",
            status="fail",
            message=(f"Too few documents ({count}), minimum {min_required} required"),
        )
    if count <= min_recommended:
        return PreflightCheck(
            name="documents",
            status="warn",
            message=(f"Low document count ({count}), {min_recommended}+ recommended"),
        )
    return PreflightCheck(
        name="documents",
        status="pass",
        message=f"{count} documents found",
    )


def _check_dependencies(
    probe: ProbeResult,
    *,
    containerised: bool,
) -> PreflightCheck:
    """Check whether fine-tuning ML dependencies are reachable.

    Args:
        probe: Effective probe outcome; for a docker-backed run the
            ephemeral container proved the image boots and its ML stack
            imports, for in-process it reflects the local install.
        containerised: Whether the probe came from a container (message
            wording only).

    Returns:
        ``PreflightCheck`` instance.
    """
    if probe.ok:
        message = (
            "ML dependencies available via ephemeral fine-tune probe"
            if containerised
            else "ML dependencies installed"
        )
        return PreflightCheck(name="dependencies", status="pass", message=message)
    return PreflightCheck(
        name="dependencies",
        status="fail",
        message=(
            "Fine-tune image probe failed"
            if containerised
            else "Missing ML dependencies"
        ),
        detail=probe.detail,
    )


def _check_gpu(probe: ProbeResult) -> PreflightCheck:
    """GPU availability check from the effective probe.

    Returns:
        ``PreflightCheck`` instance.
    """
    if not probe.ok:
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="Cannot detect GPU (dependency probe failed)",
            detail=probe.detail,
        )
    if probe.gpu_error is not None:
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="GPU detection failed -- treating as CPU-only",
            detail=probe.gpu_error,
        )
    if probe.gpu is not None:
        detail = f"VRAM: {probe.vram_gb:.1f} GB" if probe.vram_gb is not None else None
        return PreflightCheck(
            name="gpu",
            status="pass",
            message=f"GPU available: {probe.gpu}",
            detail=detail,
        )
    return PreflightCheck(
        name="gpu",
        status="warn",
        message="No GPU detected -- training will be slow",
        detail="CPU-only mode",
    )


def _recommend_batch_size(
    *,
    vram_gb: float | None,
    default_batch_size: int = FINE_TUNE_DEFAULT_BATCH_SIZE,
    vram_table: tuple[tuple[float, int], ...] = _BATCH_SIZE_BY_VRAM_GB,
) -> int:
    """Recommend batch size from the probed VRAM reading.

    Args:
        vram_gb: VRAM the effective probe saw; ``None`` means CPU-only.
        default_batch_size: Fallback returned when the VRAM tier
            table does not produce a match (CPU-only or sub-threshold
            GPU). Resolved from the
            ``memory.fine_tune_default_batch_size`` setting at the
            API boundary; imported default is the offline fallback.
        vram_table: ``(min_vram_gb, batch_size)`` rows sorted
            descending by threshold; the first row whose threshold the
            detected VRAM clears wins. Sourced from
            ``app_state.bridge_config.memory.fine_tune_vram_batch_table``
            (operator-tunable via ``memory.fine_tune_vram_batch_table``);
            the module constant is the offline/standalone fallback.

    Returns:
        Result of type ``int``.
    """
    if vram_gb is None:
        return default_batch_size
    for threshold_gb, batch_size in vram_table:
        if vram_gb >= threshold_gb:
            return batch_size
    return default_batch_size


def _check_disk_space(source_dir: str) -> PreflightCheck:
    """Check available disk space for fine-tuning output.

    Returns:
        ``PreflightCheck`` instance.
    """
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    try:
        path = Path(source_dir) if Path(source_dir).exists() else Path()
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        if free_gb < 1:
            return PreflightCheck(
                name="disk_space",
                status="fail",
                message="Insufficient disk space",
                detail=f"{free_gb:.1f} GB free",
            )
        if free_gb < 5:  # noqa: PLR2004
            return PreflightCheck(
                name="disk_space",
                status="warn",
                message="Low disk space",
                detail=f"{free_gb:.1f} GB free, 5+ GB recommended",
            )
        return PreflightCheck(
            name="disk_space",
            status="pass",
            message=f"{free_gb:.1f} GB available",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.debug(
            MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED,
            check="disk_space",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Could not check disk space: {type(exc).__name__}",
        )
