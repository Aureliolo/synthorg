"""Unit tests for :class:`BrowserToolArgs` validation."""

import pytest
from pydantic import ValidationError

from synthorg.tools.browser._args import BrowserToolArgs

pytestmark = pytest.mark.unit


def _base(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"mode": "navigate", "url": "http://example.test"}
    base.update(overrides)
    return base


class TestBrowserToolArgsCore:
    """Core acceptance + rejection cases for the args model."""

    def test_navigate_requires_url_or_path(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate({"mode": "navigate"})

    def test_navigate_with_path_is_ok(self) -> None:
        args = BrowserToolArgs.model_validate(
            {"mode": "navigate", "path": "fixture/index.html"},
        )
        assert args.path == "fixture/index.html"
        assert args.url is None

    def test_screenshot_requires_spec_and_name(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(
                _base(mode="screenshot", spec_name=None, screenshot_name=None),
            )

    def test_screenshot_with_spec_and_name_is_ok(self) -> None:
        args = BrowserToolArgs.model_validate(
            _base(
                mode="screenshot",
                spec_name="login",
                screenshot_name="hero",
            ),
        )
        assert args.spec_name == "login"
        assert args.screenshot_name == "hero"

    def test_diff_requires_screenshot_name(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(
                _base(mode="diff", spec_name="x"),
            )

    def test_diff_requires_spec_name(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(
                _base(mode="diff", screenshot_name="x"),
            )

    def test_spec_requires_both(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(
                _base(mode="spec"),
            )


class TestBrowserToolArgsFieldConstraints:
    """Constraints on individual fields."""

    def test_tolerance_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(_base(tolerance=0.0))

    def test_tolerance_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(_base(tolerance=1.1))

    def test_tolerance_valid_range_accepted(self) -> None:
        args = BrowserToolArgs.model_validate(_base(tolerance=0.97))
        assert args.tolerance == pytest.approx(0.97)

    def test_min_impact_is_literal(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(_base(min_impact="catastrophic"))

    def test_viewport_paired_dimensions(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(_base(viewport_width=800))
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(_base(viewport_height=600))

    def test_viewport_both_set_ok(self) -> None:
        args = BrowserToolArgs.model_validate(
            _base(viewport_width=800, viewport_height=600),
        )
        assert args.viewport_width == 800
        assert args.viewport_height == 600

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            BrowserToolArgs.model_validate(
                _base(unknown_extra="boom"),
            )

    def test_model_is_frozen(self) -> None:
        args = BrowserToolArgs.model_validate(_base())
        with pytest.raises(ValidationError):
            args.mode = "screenshot"
