"""Pluggable protocol for screenshot diffing.

Defines :class:`ScreenshotDiffer`, the runtime-checkable swap point for
the SSIM default in :class:`SSIMDiffer`. Alternate implementations
(perceptual hash, pixelmatch, ML-based) drop in via the BrowserTool
constructor without touching the dispatch code.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ScreenshotDiffer(Protocol):
    """Compare two screenshots and produce a similarity score.

    Implementations MUST:
      * load both images from disk
      * raise :class:`BrowserDiffError` on size mismatch or load failure
      * write a diff visualisation to disk at ``diff_output``
      * be stateless and safe for concurrent invocation from multiple
        agent tasks. The default :class:`SSIMDiffer` honours this by
        offloading the blocking compute to ``asyncio.to_thread``.
    """

    async def compare(
        self,
        *,
        baseline: Path,
        current: Path,
        tolerance: float,
        diff_output: Path,
    ) -> float:
        """Return the similarity score for the pair.

        Args:
            baseline: Absolute filesystem path to the reference image.
            current: Absolute filesystem path to the just-captured image.
            tolerance: Pass threshold; the caller decides pass / fail.
            diff_output: Absolute path to write the heatmap PNG.

        Returns:
            A score in [0.0, 1.0]; higher is more similar.
        """
        ...
