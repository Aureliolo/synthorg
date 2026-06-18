"""Screenshot reading + colour analysis helpers for vision verifiers.

Pure, deterministic image helpers shared by the heuristic verifier and
the ``llm_vision`` base64 encoder. Reading is workspace-scoped: a
``VisionScreenshotRef.workspace_path`` is resolved under the workspace
root and rejected if it escapes (``..`` / absolute components).
"""

import base64
import math
from pathlib import Path
from typing import Final

import numpy as np
from PIL import Image, UnidentifiedImageError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.vision_verify import VISION_SCREENSHOT_REJECTED
from synthorg.security.visionverify.errors import VisionScreenshotError

logger = get_logger(__name__)

_RGB_CHANNELS: Final[int] = 3
_MAX_CHANNEL_VALUE: Final[int] = 255
# Largest possible Euclidean distance between two RGB triples
# (black vs white): sqrt(3 * 255**2). Used to normalise distances 0..1.
_MAX_RGB_DISTANCE: Final[float] = math.sqrt(_RGB_CHANNELS * (_MAX_CHANNEL_VALUE**2))


def resolve_screenshot(workspace: Path, workspace_path: str) -> Path:
    """Resolve ``workspace_path`` under ``workspace``, rejecting traversal.

    Returns:
        The resolved absolute path under the workspace root.

    Raises:
        VisionScreenshotError: If the path escapes the workspace or does
            not exist.
    """
    if ".." in Path(workspace_path).parts:
        msg = "screenshot path must not contain '..' segments"
        logger.warning(
            VISION_SCREENSHOT_REJECTED,
            reason="traversal_segment",
            error_type=VisionScreenshotError.__name__,
        )
        raise VisionScreenshotError(msg, context={"path": workspace_path})
    candidate = (workspace / workspace_path).resolve()
    root = workspace.resolve()
    if not candidate.is_relative_to(root):
        msg = "screenshot path must resolve under the workspace root"
        logger.warning(
            VISION_SCREENSHOT_REJECTED,
            reason="escapes_workspace_root",
            error_type=VisionScreenshotError.__name__,
        )
        raise VisionScreenshotError(msg, context={"path": workspace_path})
    if not candidate.is_file():
        msg = "screenshot file does not exist"
        logger.warning(
            VISION_SCREENSHOT_REJECTED,
            reason="file_missing",
            error_type=VisionScreenshotError.__name__,
        )
        raise VisionScreenshotError(msg, context={"path": workspace_path})
    return candidate


def read_png_base64(path: Path) -> str:
    """Return the base64-encoded bytes of the PNG at ``path``."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def mean_rgb(path: Path) -> tuple[int, int, int]:
    """Return the mean (r, g, b) colour of the image at ``path``.

    Raises:
        VisionScreenshotError: If the file cannot be decoded as an image.
    """
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            array = np.asarray(rgb, dtype=np.float64)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        msg = "screenshot could not be decoded as an image"
        logger.warning(
            VISION_SCREENSHOT_REJECTED,
            reason="decode_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise VisionScreenshotError(
            msg,
            context={"error_type": type(exc).__name__},
        ) from exc
    means = array.reshape(-1, _RGB_CHANNELS).mean(axis=0).round().astype(int)
    return (int(means[0]), int(means[1]), int(means[2]))


def normalised_rgb_distance(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
) -> float:
    """Return the 0..1 normalised Euclidean distance between two colours."""
    squared = sum((a[i] - b[i]) ** 2 for i in range(_RGB_CHANNELS))
    return math.sqrt(squared) / _MAX_RGB_DISTANCE
