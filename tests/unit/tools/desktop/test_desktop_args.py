"""Unit tests for DesktopToolArgs per-mode validation."""

import pytest
from pydantic import ValidationError

from synthorg.tools.desktop._args import DesktopToolArgs

pytestmark = pytest.mark.unit


class TestDesktopArgsValid:
    def test_launch_requires_app_command(self) -> None:
        with pytest.raises(ValidationError, match="app_command"):
            DesktopToolArgs(mode="launch")

    def test_launch_ok(self) -> None:
        args = DesktopToolArgs(mode="launch", app_command="python3 /workspace/app.py")
        assert args.app_command == "python3 /workspace/app.py"

    def test_launch_rejects_whitespace_only_app_command(self) -> None:
        with pytest.raises(ValidationError):
            DesktopToolArgs(mode="launch", app_command="   ")

    def test_click_requires_coordinates(self) -> None:
        with pytest.raises(ValidationError, match="x and y"):
            DesktopToolArgs(mode="click", x=10)

    def test_click_ok(self) -> None:
        args = DesktopToolArgs(mode="click", x=10, y=20, button=3, double=True)
        assert (args.x, args.y, args.button, args.double) == (10, 20, 3, True)

    def test_type_requires_text(self) -> None:
        with pytest.raises(ValidationError, match="text"):
            DesktopToolArgs(mode="type")

    def test_type_allows_empty_string(self) -> None:
        args = DesktopToolArgs(mode="type", text="")
        assert args.text == ""

    def test_key_requires_keys(self) -> None:
        with pytest.raises(ValidationError, match="keys"):
            DesktopToolArgs(mode="key")

    def test_screenshot_requires_name(self) -> None:
        with pytest.raises(ValidationError, match="screenshot_name"):
            DesktopToolArgs(mode="screenshot")

    def test_scroll_requires_direction(self) -> None:
        with pytest.raises(ValidationError, match="direction"):
            DesktopToolArgs(mode="scroll")

    def test_scroll_ok(self) -> None:
        args = DesktopToolArgs(mode="scroll", direction="down", amount=5)
        assert (args.direction, args.amount) == ("down", 5)


class TestDesktopArgsBounds:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            DesktopToolArgs(mode="screenshot", screenshot_name="x", bogus=1)  # type: ignore[call-arg]

    def test_button_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            DesktopToolArgs(mode="click", x=1, y=1, button=9)

    def test_coordinate_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DesktopToolArgs(mode="click", x=-1, y=1)

    def test_frozen(self) -> None:
        args = DesktopToolArgs(mode="type", text="hi")
        with pytest.raises(ValidationError):
            args.text = "bye"
