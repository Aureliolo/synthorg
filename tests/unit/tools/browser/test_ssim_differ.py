"""Unit tests for the default :class:`SSIMDiffer`.

Exercises the real Pillow + scikit-image compute path so a future
refactor that breaks the offloading or the heatmap write surfaces in
unit tests rather than the simulation harness.
"""

from pathlib import Path

import pytest

from synthorg.tools.browser._ssim_differ import SSIMDiffer
from synthorg.tools.browser.errors import BrowserDiffError

pytestmark = pytest.mark.unit


def _write_grey_png(path: Path, *, value: int, size: tuple[int, int]) -> None:
    """Write a uniform-grey PNG so SSIM is deterministic across OSes."""
    from PIL import Image

    img = Image.new("L", size, color=value)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


class TestSSIMDifferCore:
    async def test_identical_images_score_is_one(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        _write_grey_png(baseline, value=128, size=(32, 32))
        _write_grey_png(current, value=128, size=(32, 32))
        differ = SSIMDiffer()
        diff_out = tmp_path / "diff.png"
        score = await differ.compare(
            baseline=baseline,
            current=current,
            tolerance=0.98,
            diff_output=diff_out,
        )
        assert score == pytest.approx(1.0)
        assert diff_out.exists()

    async def test_size_mismatch_raises(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        _write_grey_png(baseline, value=128, size=(32, 32))
        _write_grey_png(current, value=128, size=(64, 64))
        differ = SSIMDiffer()
        with pytest.raises(BrowserDiffError):
            await differ.compare(
                baseline=baseline,
                current=current,
                tolerance=0.98,
                diff_output=tmp_path / "diff.png",
            )

    async def test_missing_baseline_raises(self, tmp_path: Path) -> None:
        baseline = tmp_path / "absent.png"
        current = tmp_path / "current.png"
        _write_grey_png(current, value=128, size=(32, 32))
        differ = SSIMDiffer()
        with pytest.raises(BrowserDiffError):
            await differ.compare(
                baseline=baseline,
                current=current,
                tolerance=0.98,
                diff_output=tmp_path / "diff.png",
            )
