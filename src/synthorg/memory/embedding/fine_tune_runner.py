"""Fine-tune pipeline container entrypoint.

Reads the flat stage configuration from the
``SYNTHORG_FINE_TUNE_STAGE_CONFIG`` env var (inline JSON injected by
the backend's ephemeral container launcher), executes the requested
torch-bound stage via the shared dispatch, and emits structured
markers on stdout/stderr for the launcher to parse:
``STAGE_START:<stage>`` / ``STAGE_COMPLETE:<stage>`` bracket the run,
``PROGRESS:<fraction>`` drives the orchestrator's WS progress
pipeline, and ``ERROR:<message>`` carries the failure detail.

With ``SYNTHORG_FINE_TUNE_PROBE=1`` the runner instead performs a
dependency/GPU readiness probe: it imports the ML stack, inspects CUDA,
prints one ``PROBE_OK gpu=<name|none> vram_gb=<x>`` or
``PROBE_FAIL <reason>`` line, and exits (the preflight endpoint boots
an ephemeral probe container around this mode).

Designed to run as ``python -m synthorg.memory.embedding.fine_tune_runner``
inside the ``synthorg-fine-tune-gpu`` (default) or
``synthorg-fine-tune-cpu`` container. Both ship the same Python entry
point; they differ only in the bundled torch build (CUDA vs CPU).

Uses ``print()`` for the structured markers -- this is an entrypoint
script whose stdout IS the wire protocol, not application library code.
"""

import asyncio
import json
import os
import signal
import sys
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.observability import safe_error_description

_STAGE_CONFIG_ENV: Final[str] = "SYNTHORG_FINE_TUNE_STAGE_CONFIG"
_PROBE_ENV: Final[str] = "SYNTHORG_FINE_TUNE_PROBE"
_BYTES_PER_GIB: Final[int] = 1024**3
# Progress marker throttle: only emit when the fraction moved at least
# this much, so a chatty stage cannot flood the log stream the launcher
# parses.
_PROGRESS_EMIT_STEP: Final[float] = 0.01


def _load_config() -> dict[str, object] | None:
    """Load and validate the stage config from the inline env var.

    Returns:
        Parsed config dict, or ``None`` on failure.
    """
    raw = os.environ.get(_STAGE_CONFIG_ENV)
    if raw is None or not raw.strip():
        print(  # noqa: T201
            f"ERROR: {_STAGE_CONFIG_ENV} is not set; the launcher injects"
            " the stage config as inline JSON",
            file=sys.stderr,
        )
        return None
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid config JSON: {exc.msg}", file=sys.stderr)  # noqa: T201
        return None
    if not isinstance(config, dict):
        print("ERROR: config must be a JSON object", file=sys.stderr)  # noqa: T201
        return None
    return config


def _make_progress_printer() -> _ProgressPrinter:
    """Build the throttled ``PROGRESS:`` marker emitter.

    Returns:
        Result of type ``_ProgressPrinter``.
    """
    return _ProgressPrinter()


class _ProgressPrinter:
    """Emits ``PROGRESS:<fraction>`` markers, throttled by step size."""

    def __init__(self) -> None:
        self._last_emitted = -1.0

    def __call__(self, fraction: float) -> None:
        """Print the marker when the fraction moved enough (or hit 1.0)."""
        if fraction >= 1.0 or fraction - self._last_emitted >= _PROGRESS_EMIT_STEP:
            self._last_emitted = fraction
            print(f"PROGRESS:{fraction:.4f}", flush=True)  # noqa: T201


def _run_probe() -> int:
    """Report dependency/GPU readiness with one parseable output line.

    Returns:
        ``0`` on ``PROBE_OK``, ``1`` on ``PROBE_FAIL``.
    """
    from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
        _import_sentence_transformers,
        _import_torch,
    )
    from synthorg.memory.embedding.fine_tune_trainer import (  # noqa: PLC0415
        _import_trainer_api,
    )

    try:
        torch = _import_torch()
        _import_sentence_transformers()
        # The training half of the extra is separately installable and was
        # separately missing: the trainer's `datasets` and `accelerate` are
        # absent from sentence-transformers' own dependency list. Probing only
        # the package reports ready for a stack that cannot reach stage 3.
        _import_trainer_api()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        print(  # noqa: T201
            f"PROBE_FAIL ML dependencies failed to import:"
            f" {safe_error_description(exc)}",
            flush=True,
        )
        return 1
    gpu = "none"
    vram_gb = 0.0
    try:
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            gpu = str(props.name)
            vram_gb = props.total_memory / _BYTES_PER_GIB
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        print(  # noqa: T201
            f"PROBE_FAIL CUDA inspection failed: {safe_error_description(exc)}",
            flush=True,
        )
        return 1
    print(f"PROBE_OK gpu={gpu} vram_gb={vram_gb:.1f}", flush=True)  # noqa: T201
    return 0


def _run() -> int:
    """Execute the fine-tune stage (or probe) and return an exit code.

    Returns:
        Result of type ``int``.
    """
    if os.environ.get(_PROBE_ENV, "").strip() == "1":
        return _run_probe()

    config = _load_config()
    if config is None:
        return 1

    stage_name = config.get("stage", "")
    try:
        stage = FineTuneStage(str(stage_name))
    except ValueError:
        print(f"ERROR: unknown stage {stage_name!r}", file=sys.stderr)  # noqa: T201
        return 1

    # Lazy import so the probe path never pulls the dispatch machinery.
    from synthorg.memory.embedding.fine_tune_stage_dispatch import (  # noqa: PLC0415
        CONTAINER_STAGES,
        dispatch_stage,
    )

    if stage not in CONTAINER_STAGES:
        print(  # noqa: T201
            f"ERROR: stage {stage_name!r} is not container-executable",
            file=sys.stderr,
        )
        return 1

    # Cooperative cancellation via SIGTERM (docker stop).
    token = CancellationToken()
    prev_handler = signal.signal(signal.SIGTERM, lambda *_: token.cancel())

    try:
        print(f"STAGE_START:{stage_name}", flush=True)  # noqa: T201
        try:
            asyncio.run(
                dispatch_stage(
                    stage,
                    config,
                    token,
                    progress_callback=_make_progress_printer(),
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            print(  # noqa: T201
                f"ERROR: {stage_name} failed: {safe_error_description(exc)}",
                file=sys.stderr,
            )
            return 1

        print(f"STAGE_COMPLETE:{stage_name}", flush=True)  # noqa: T201
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_handler)


if __name__ == "__main__":
    sys.exit(_run())
