"""Unit tests for vision verifier strategies and the factory."""

from pathlib import Path

import pytest
from PIL import Image

from synthorg.core.enums import AutonomyLevel
from synthorg.security.config import VisionVerifierKind, VisionVerifyConfig
from synthorg.security.visionverify.errors import (
    VisionScreenshotError,
    VisionVerifyConfigError,
)
from synthorg.security.visionverify.factory import build_vision_verifier
from synthorg.security.visionverify.models import (
    VisionReviewInput,
    VisionScreenshotRef,
    VisionSeverity,
    VisualExpectation,
    VisualExpectationKind,
)
from synthorg.security.visionverify.verifiers import (
    HeuristicVisionVerifier,
    LLMVisionVerifier,
    NoOpVisionVerifier,
)
from synthorg.security.visionverify.verifiers._image import (
    mean_rgb,
    resolve_screenshot,
)

pytestmark = pytest.mark.unit

_SHA = "b" * 64
_SCREENSHOT_REL = ".synthorg/desktop/screenshots/shot.png"


def _write_png(workspace: Path, rgb: tuple[int, int, int]) -> VisionScreenshotRef:
    path = workspace / _SCREENSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), rgb).save(path)
    return VisionScreenshotRef(workspace_path=_SCREENSHOT_REL, sha256=_SHA)


def _input(
    ref: VisionScreenshotRef,
    *,
    expectations: tuple[VisualExpectation, ...] = (),
) -> VisionReviewInput:
    return VisionReviewInput(
        task_id="t1",
        execution_id="e1",
        brief="a blue background",
        acceptance_criteria=("background is blue",),
        screenshots=(ref,),
        expectations=expectations,
        generator_agent_id="gen",
        evaluator_agent_id="vision",
        autonomy=AutonomyLevel.SUPERVISED,
    )


class TestNoOpVerifier:
    async def test_returns_clean_report(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path, (255, 0, 0))
        report = await NoOpVisionVerifier().verify(_input(ref))
        assert report.findings == ()
        assert report.verifier_kind == "noop"


class TestHeuristicVerifier:
    async def test_flags_colour_mismatch(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path, (220, 20, 20))  # red app
        expectation = VisualExpectation(
            kind=VisualExpectationKind.DOMINANT_COLOUR,
            description="background should be blue",
            expected_rgb=(0, 0, 255),
            tolerance=0.15,
        )
        verifier = HeuristicVisionVerifier(workspace=tmp_path)
        report = await verifier.verify(_input(ref, expectations=(expectation,)))
        assert len(report.findings) == 1
        assert report.findings[0].severity is VisionSeverity.HIGH
        assert "blue" in report.findings[0].description

    async def test_passes_on_colour_match(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path, (10, 10, 240))  # blue app
        expectation = VisualExpectation(
            kind=VisualExpectationKind.DOMINANT_COLOUR,
            description="background should be blue",
            expected_rgb=(0, 0, 255),
            tolerance=0.15,
        )
        verifier = HeuristicVisionVerifier(workspace=tmp_path)
        report = await verifier.verify(_input(ref, expectations=(expectation,)))
        assert report.findings == ()

    async def test_no_expectations_passes(self, tmp_path: Path) -> None:
        ref = _write_png(tmp_path, (123, 45, 67))
        verifier = HeuristicVisionVerifier(workspace=tmp_path)
        report = await verifier.verify(_input(ref))
        assert report.findings == ()

    def test_requires_absolute_workspace(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            HeuristicVisionVerifier(workspace=Path("rel"))


class TestFactory:
    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        config = VisionVerifyConfig(enabled=False)
        assert build_vision_verifier(config, workspace=tmp_path) is None

    def test_noop_dispatch(self, tmp_path: Path) -> None:
        config = VisionVerifyConfig(enabled=True, verifier_kind=VisionVerifierKind.NOOP)
        verifier = build_vision_verifier(config, workspace=tmp_path)
        assert isinstance(verifier, NoOpVisionVerifier)

    def test_heuristic_dispatch(self, tmp_path: Path) -> None:
        config = VisionVerifyConfig(
            enabled=True,
            verifier_kind=VisionVerifierKind.HEURISTIC,
        )
        verifier = build_vision_verifier(config, workspace=tmp_path)
        assert isinstance(verifier, HeuristicVisionVerifier)

    def test_llm_vision_missing_provider_raises(self, tmp_path: Path) -> None:
        config = VisionVerifyConfig(
            enabled=True,
            verifier_kind=VisionVerifierKind.LLM_VISION,
        )
        with pytest.raises(VisionVerifyConfigError, match="CompletionProvider"):
            build_vision_verifier(config, workspace=tmp_path)

    def test_llm_vision_tier_resolves_none_raises(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from synthorg.providers.protocol import CompletionProvider
        from tests._shared.mock_of import mock_of

        provider = mock_of[CompletionProvider](
            complete=AsyncMock(spec=CompletionProvider.complete),
            get_model_capabilities=AsyncMock(
                spec=CompletionProvider.get_model_capabilities,
            ),
        )
        config = VisionVerifyConfig(
            enabled=True,
            verifier_kind=VisionVerifierKind.LLM_VISION,
        )
        with pytest.raises(VisionVerifyConfigError, match=r"no\s+model"):
            build_vision_verifier(
                config,
                workspace=tmp_path,
                provider=provider,
                tier_resolver=lambda _tier: None,
            )

    def test_llm_vision_dispatch(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from synthorg.providers.protocol import CompletionProvider
        from tests._shared.mock_of import mock_of

        provider = mock_of[CompletionProvider](
            complete=AsyncMock(spec=CompletionProvider.complete),
            get_model_capabilities=AsyncMock(
                spec=CompletionProvider.get_model_capabilities,
            ),
        )
        config = VisionVerifyConfig(
            enabled=True,
            verifier_kind=VisionVerifierKind.LLM_VISION,
        )
        verifier = build_vision_verifier(
            config,
            workspace=tmp_path,
            provider=provider,
            tier_resolver=lambda _tier: "example-medium-001",
        )
        assert isinstance(verifier, LLMVisionVerifier)


class TestImageHelpers:
    def test_resolve_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VisionScreenshotError, match="does not exist"):
            resolve_screenshot(tmp_path, "absent.png")

    def test_resolve_rejects_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(VisionScreenshotError, match="must not contain"):
            resolve_screenshot(tmp_path, "../escape.png")

    def test_resolve_rejects_escape_outside_workspace(self, tmp_path: Path) -> None:
        # An absolute path that resolves outside the workspace is rejected.
        outside = tmp_path.parent / "outside.png"
        with pytest.raises(VisionScreenshotError, match="under the workspace"):
            resolve_screenshot(tmp_path, str(outside))

    def test_mean_rgb_rejects_non_image(self, tmp_path: Path) -> None:
        bogus = tmp_path / "not-an-image.png"
        bogus.write_bytes(b"this is not a PNG")
        with pytest.raises(VisionScreenshotError, match="could not be decoded"):
            mean_rgb(bogus)

    def test_mean_rgb_solid_colour(self, tmp_path: Path) -> None:
        path = tmp_path / "solid.png"
        Image.new("RGB", (8, 8), (10, 20, 30)).save(path)
        assert mean_rgb(path) == (10, 20, 30)
