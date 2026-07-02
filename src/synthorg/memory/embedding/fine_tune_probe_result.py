# module-kind: code
"""Probe verdict model + parsing for the fine-tune image probe.

The runner prints one ``PROBE_OK gpu=<name|none> vram_gb=<x>`` or
``PROBE_FAIL <reason>`` line; the launcher extracts and parses it here.
Split from the container launcher so the preflight layers can depend on
the verdict shape without the aiodocker machinery.
"""

import re
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.observability import get_logger
from synthorg.observability.events.fine_tune import (
    FINE_TUNE_PROBE_FAILED,
    FINE_TUNE_PROBE_OK,
)

logger = get_logger(__name__)

_PROBE_LINE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^PROBE_(?:OK|FAIL)\b.*$", re.MULTILINE
)


class ProbeResult(BaseModel):
    """Outcome of an ephemeral fine-tune image probe.

    Attributes:
        ok: Whether the image booted and its dependencies imported.
        gpu: GPU device name the container saw, or ``None``.
        vram_gb: Total VRAM in GiB, or ``None`` when no GPU.
        detail: Human-readable outcome line for the preflight report.
        gpu_error: Failure detail when the dependencies imported but
            the GPU inspection itself raised; distinguishes "detection
            broke" from a genuine CPU-only host.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ok: bool
    gpu: str | None = None
    vram_gb: float | None = Field(default=None, ge=0)
    detail: str
    gpu_error: str | None = None

    @model_validator(mode="after")
    def _failed_probe_carries_no_hardware(self) -> Self:
        """A failed probe cannot report GPU readings.

        Returns:
            The validated instance.

        Raises:
            ValueError: When ``ok=False`` carries gpu/vram values.
        """
        if not self.ok and (self.gpu is not None or self.vram_gb is not None):
            msg = "a failed probe cannot carry gpu/vram readings"
            raise ValueError(msg)
        return self


def parse_probe_line(line: str) -> ProbeResult:
    """Parse the runner's ``PROBE_OK`` / ``PROBE_FAIL`` output line.

    Returns:
        Result of type ``ProbeResult``.
    """
    text = line.strip()
    if text.startswith("PROBE_OK"):
        gpu: str | None = None
        vram: float | None = None
        rest = text.removeprefix("PROBE_OK").strip()
        if " vram_gb=" in rest:
            gpu_part, _, vram_part = rest.rpartition(" vram_gb=")
            gpu_value = gpu_part.removeprefix("gpu=").strip()
            gpu = None if gpu_value in {"", "none"} else gpu_value
            try:
                vram = float(vram_part)
            except ValueError:
                vram = None
            if gpu is None:
                vram = None
        return ProbeResult(ok=True, gpu=gpu, vram_gb=vram, detail=text)
    reason = text.removeprefix("PROBE_FAIL").strip() or "probe failed"
    return ProbeResult(ok=False, detail=reason)


def parse_probe_output(output: str, *, image: str) -> ProbeResult:
    """Extract and log the probe verdict from the container output.

    Returns:
        Result of type ``ProbeResult``.
    """
    match = _PROBE_LINE_PATTERN.search(output)
    if match is None:
        detail = "probe produced no PROBE_OK/PROBE_FAIL line"
        logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
        return ProbeResult(ok=False, detail=detail)
    result = parse_probe_line(match.group(0))
    if result.ok:
        logger.info(
            FINE_TUNE_PROBE_OK,
            image=image,
            gpu=result.gpu,
            vram_gb=result.vram_gb,
        )
    else:
        logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=result.detail)
    return result
