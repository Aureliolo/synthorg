"""Fine-tune preflight thresholds, checks, and batch-size recommendation.

Pure helper module for the memory fine-tune controller: resolves
operator-tunable thresholds, runs the (sync, thread-offloaded) document /
GPU / dependency / disk preflight checks, and recommends a batch size from
detected VRAM. No Litestar surface; the controller imports these.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    PreflightCheck,
)
from synthorg.memory.embedding.fine_tune_run_helpers import build_config
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED,
    MEMORY_FINE_TUNE_THRESHOLD_FALLBACK,
)
from synthorg.settings.definitions.memory import (
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


def _run_preflight_checks(
    request: FineTuneRequest,
    *,
    min_required: int = FINE_TUNE_MIN_DOCS_REQUIRED,
    min_recommended: int = FINE_TUNE_MIN_DOCS_RECOMMENDED,
    max_depth: int = FINE_TUNE_PREFLIGHT_MAX_DEPTH,
    walk_timeout_s: float = FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S,
) -> list[PreflightCheck]:
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

    Returns:
        List of the declared element type.
    """
    checks: list[PreflightCheck] = []
    checks.append(_check_dependencies())
    checks.append(_check_gpu())
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
    return checks


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


_FINE_TUNE_SIDECAR_HEALTH_HOST: Final[str] = "fine-tune"
_FINE_TUNE_SIDECAR_HEALTH_TIMEOUT_S: Final[float] = 1.5
_HTTP_STATUS_OK_MIN: Final[int] = 200
_HTTP_STATUS_OK_MAX_EXCLUSIVE: Final[int] = 300


def _check_fine_tune_sidecar_health() -> bool:
    """Best-effort probe of the fine-tune sidecar's HTTP health endpoint.

    In a Docker-orchestrated install the heavy ML deps (torch +
    sentence-transformers) live exclusively inside the
    ``synthorg-fine-tune-{gpu,cpu}`` sidecar container; the main backend
    container intentionally does NOT bundle them.  Pip-only deployments
    install the extras directly into the same process.  This helper
    covers the Docker case: when the sidecar answers its health probe,
    the deps are reachable even though ``import torch`` would fail
    locally.  Any error (DNS miss, refused connection, non-200, timeout)
    is swallowed so the caller falls back to the in-process import.

    The probe port is resolved from ``SYNTHORG_FINE_TUNE_HEALTH_PORT``
    via the same :func:`resolve_health_port` the sidecar uses to bind, so
    an operator override is honoured. A malformed override means the
    sidecar itself never bound, so the probe correctly reports failure.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    from synthorg.memory.embedding.fine_tune_runner import (  # noqa: PLC0415
        resolve_health_port,
    )

    try:
        port = resolve_health_port()
    except ValueError:
        return False
    url = f"http://{_FINE_TUNE_SIDECAR_HEALTH_HOST}:{port}/healthz"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(  # noqa: S310
            req,
            timeout=_FINE_TUNE_SIDECAR_HEALTH_TIMEOUT_S,
        ) as resp:
            status: int = resp.status
            return _HTTP_STATUS_OK_MIN <= status < _HTTP_STATUS_OK_MAX_EXCLUSIVE
    except urllib.error.URLError, TimeoutError, OSError:
        return False
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        return False


def _check_dependencies() -> PreflightCheck:
    """Check whether fine-tuning ML dependencies are reachable.

    Two-stage check: an in-process import covers pip installs that
    bundle the extras locally; an HTTP probe of the fine-tune sidecar
    container covers the Docker orchestration case where torch +
    sentence-transformers live exclusively in the sidecar image.
    Either path succeeding is enough to call the dependencies
    available.  Previously, only the in-process import was attempted,
    so every Docker-orchestrated install reported "Fine-tuning not
    enabled" regardless of whether the user had set ``fine_tuning=true``
    in the CLI config and started the sidecar.

    Returns:
        ``PreflightCheck`` instance.
    """
    try:
        from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
            _import_sentence_transformers,
            _import_torch,
        )

        _import_torch()
        _import_sentence_transformers()
    except (ImportError, FineTuneDependencyError) as exc:
        # In-process imports failed; this is the expected path for the
        # Docker orchestration where ML deps live in a sidecar.  Probe
        # the sidecar's HTTP health endpoint before declaring failure.
        if _check_fine_tune_sidecar_health():
            return PreflightCheck(
                name="dependencies",
                status="pass",
                message="ML dependencies available via fine-tune sidecar",
            )
        return PreflightCheck(
            name="dependencies",
            status="fail",
            message="Missing ML dependencies",
            detail=safe_error_description(exc),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        return PreflightCheck(
            name="dependencies",
            status="fail",
            message=f"Dependency check failed: {type(exc).__name__}",
            detail=safe_error_description(exc),
        )
    return PreflightCheck(
        name="dependencies",
        status="pass",
        message="ML dependencies installed",
    )


def _check_gpu() -> PreflightCheck:
    """Best-effort GPU availability check.

    Returns:
        ``PreflightCheck`` instance.
    """
    try:
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            vram_gb = props.total_memory / (1024**3)
            return PreflightCheck(
                name="gpu",
                status="pass",
                message=f"GPU available: {props.name}",
                detail=f"VRAM: {vram_gb:.1f} GB",
            )
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="No GPU detected -- training will be slow",
            detail="CPU-only mode",
        )
    except ImportError:
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="Cannot detect GPU (torch not installed)",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        return PreflightCheck(
            name="gpu",
            status="warn",
            message=f"GPU detection error: {type(exc).__name__}",
            detail=safe_error_description(exc),
        )


def _recommend_batch_size(
    *,
    default_batch_size: int = FINE_TUNE_DEFAULT_BATCH_SIZE,
    vram_table: tuple[tuple[float, int], ...] = _BATCH_SIZE_BY_VRAM_GB,
) -> int | None:
    """Recommend batch size based on available VRAM.

    Args:
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
        The ``int`` value when present, ``None`` otherwise.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            return default_batch_size
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024**3)
        for threshold_gb, batch_size in vram_table:
            if vram_gb >= threshold_gb:
                return batch_size
        return default_batch_size  # noqa: TRY300
    except MemoryError, RecursionError:
        raise
    except ImportError:
        # torch is optional -- absence is expected on CPU-only installs.
        return None
    except Exception as exc:  # noqa: BLE001 -- best-effort probe: log and continue
        # Drop ``exc_info=True``.  The full traceback bypasses
        # ``safe_error_description`` and can leak environment paths /
        # backend metadata; the redacted form is sufficient for triage.
        logger.warning(
            MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


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
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Could not check disk space: {type(exc).__name__}",
        )
