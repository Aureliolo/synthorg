"""Unit tests for the response models of the headless browser tool."""

import pytest
from pydantic import ValidationError

from synthorg.tools.browser._models import (
    A11yScanResult,
    A11yViolation,
    NavigationResult,
    ScreenshotDiffResult,
    ScreenshotMetadata,
    SpecResult,
)

pytestmark = pytest.mark.unit


def _violation() -> A11yViolation:
    return A11yViolation(
        rule_id="button-name",
        impact="serious",
        description="Buttons must have discernible text.",
        help_url="https://dequeuniversity.com/rules/axe/4.10/button-name",
        affected_nodes=2,
    )


def _navigation() -> NavigationResult:
    return NavigationResult(
        requested_url="file:///workspace/index.html",
        final_url="file:///workspace/index.html",
        status_code=200,
        duration_seconds=0.42,
    )


def _screenshot() -> ScreenshotMetadata:
    return ScreenshotMetadata(
        saved_path=".synthorg/screenshots/spec/foo.current.png",
        width=1280,
        height=720,
        file_size_bytes=2048,
        full_page=False,
        captured_at_iso="2026-05-20T12:00:00+00:00",
        sha256="a" * 64,
    )


def _diff() -> ScreenshotDiffResult:
    return ScreenshotDiffResult(
        spec_name="spec",
        screenshot_name="foo",
        ssim_score=0.99,
        tolerance=0.98,
        passed_tolerance=True,
        baseline_path=".synthorg/screenshots/spec/foo.png",
        current_path=".synthorg/screenshots/spec/foo.current.png",
        diff_image_path=".synthorg/screenshots/spec/foo.diff.png",
        is_baseline_new=False,
    )


def _a11y() -> A11yScanResult:
    return A11yScanResult(
        url="file:///workspace/index.html",
        min_impact="serious",
        violations=(),
        warnings=(),
        total_affected_nodes=0,
        scan_duration_seconds=0.1,
        axe_version="4.10.2",
        passed=True,
    )


class TestModelInvariants:
    def test_violation_round_trip(self) -> None:
        original = _violation()
        roundtrip = A11yViolation.model_validate(original.model_dump())
        assert roundtrip == original

    def test_navigation_status_bounds(self) -> None:
        with pytest.raises(ValidationError):
            NavigationResult.model_validate(
                {
                    "requested_url": "x",
                    "final_url": "y",
                    "status_code": 1000,
                    "duration_seconds": 0.0,
                },
            )

    def test_screenshot_dimensions_positive(self) -> None:
        with pytest.raises(ValidationError):
            ScreenshotMetadata.model_validate(
                {
                    **_screenshot().model_dump(),
                    "width": 0,
                },
            )

    def test_diff_score_range(self) -> None:
        with pytest.raises(ValidationError):
            ScreenshotDiffResult.model_validate(
                {**_diff().model_dump(), "ssim_score": 1.5},
            )

    def test_diff_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ScreenshotDiffResult.model_validate(
                {**_diff().model_dump(), "stray": 1},
            )

    def test_spec_aggregates(self) -> None:
        result = SpecResult(
            spec_name="spec",
            viewport_width=1280,
            viewport_height=720,
            navigation=_navigation(),
            screenshot=_screenshot(),
            diff=_diff(),
            accessibility=_a11y(),
            passed_all_checks=True,
        )
        dumped = result.model_dump()
        assert dumped["diff"]["passed_tolerance"] is True
        assert dumped["accessibility"]["passed"] is True

    def test_diff_passed_tolerance_invariant(self) -> None:
        # passed_tolerance must match ssim_score >= tolerance.
        with pytest.raises(ValidationError):
            ScreenshotDiffResult(
                spec_name="spec",
                screenshot_name="foo",
                ssim_score=0.5,
                tolerance=0.98,
                passed_tolerance=True,
                baseline_path="b.png",
                current_path="c.png",
                diff_image_path=None,
                is_baseline_new=False,
            )

    def test_a11y_passed_invariant(self) -> None:
        # passed must equal (no violations).
        with pytest.raises(ValidationError):
            A11yScanResult(
                url="x",
                min_impact="serious",
                violations=(_violation(),),
                warnings=(),
                total_affected_nodes=2,
                scan_duration_seconds=0.0,
                axe_version="4.10.2",
                passed=True,
            )

    def test_a11y_total_affected_invariant(self) -> None:
        with pytest.raises(ValidationError):
            A11yScanResult(
                url="x",
                min_impact="serious",
                violations=(_violation(),),
                warnings=(),
                total_affected_nodes=99,
                scan_duration_seconds=0.0,
                axe_version="4.10.2",
                passed=False,
            )

    def test_spec_passed_all_invariant(self) -> None:
        # passed_all_checks must match diff AND a11y.
        with pytest.raises(ValidationError):
            SpecResult(
                spec_name="spec",
                viewport_width=1280,
                viewport_height=720,
                navigation=_navigation(),
                screenshot=_screenshot(),
                diff=_diff(),
                accessibility=_a11y(),
                passed_all_checks=False,
            )

    def test_screenshot_sha256_pattern(self) -> None:
        with pytest.raises(ValidationError):
            ScreenshotMetadata.model_validate(
                {**_screenshot().model_dump(), "sha256": "not-hex"},
            )

    def test_violation_collections_frozen(self) -> None:
        a11y = _a11y()
        with pytest.raises(ValidationError):
            a11y.violations = (_violation(),)
