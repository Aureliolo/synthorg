"""SSIM-based screenshot differ.

Default :class:`ScreenshotDiffer`. Uses scikit-image's
``structural_similarity`` so subpixel antialiasing differences across
runs / OSes do not flake the agent iteration loop.
"""

from pathlib import Path  # noqa: TC003 -- runtime use in image open
from typing import Any, cast

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_DIFF_FAILED,
)
from synthorg.tools.browser._constants import SSIM_DATA_RANGE
from synthorg.tools.browser.errors import BrowserDiffError

logger = get_logger(__name__)


class SSIMDiffer:
    """Default differ; structural similarity index against a baseline.

    Computes SSIM on grayscale luminance and writes a normalised
    heatmap PNG (1 - per-pixel SSIM, scaled to 0-255) sibling to the
    baseline so the agent can fetch a visual artefact when reasoning
    about a regression.
    """

    async def compare(
        self,
        *,
        baseline: Path,
        current: Path,
        tolerance: float,
        diff_output: Path,
    ) -> float:
        """Return SSIM score in [0.0, 1.0]."""
        del tolerance  # caller compares; differ only computes the score
        try:
            import numpy as np  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415
            from skimage.metrics import (  # noqa: PLC0415
                structural_similarity,
            )
        except ImportError as exc:
            raise BrowserDiffError(
                "Screenshot diff requires scikit-image, pillow, numpy",
                context={"error": safe_error_description(exc)},
            ) from exc

        if not baseline.exists():
            raise BrowserDiffError(
                "Baseline screenshot not found on disk",
                context={"baseline": str(baseline)},
            )
        if not current.exists():
            raise BrowserDiffError(
                "Current screenshot not found on disk",
                context={"current": str(current)},
            )

        try:
            with Image.open(baseline) as a_img, Image.open(current) as b_img:
                if a_img.size != b_img.size:
                    raise BrowserDiffError(
                        "Screenshot dimensions differ",
                        context={
                            "baseline_size": a_img.size,
                            "current_size": b_img.size,
                        },
                    )
                gray_a = np.asarray(a_img.convert("L"))
                gray_b = np.asarray(b_img.convert("L"))

            ssim_call = cast(
                "Any",
                structural_similarity,
            )
            score, per_pixel = ssim_call(
                gray_a,
                gray_b,
                data_range=SSIM_DATA_RANGE,
                full=True,
            )

            heatmap = ((1.0 - per_pixel.clip(0.0, 1.0)) * SSIM_DATA_RANGE).astype(
                np.uint8
            )
            diff_output.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(heatmap, mode="L").save(diff_output)

            return float(score)

        except BrowserDiffError:
            raise
        except Exception as exc:
            logger.warning(
                BROWSER_DIFF_FAILED,
                baseline=str(baseline),
                current=str(current),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise BrowserDiffError(
                "SSIM comparison failed",
                context={"error_type": type(exc).__name__},
            ) from exc
