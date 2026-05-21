"""Unit tests for pure helpers in the in-container desktop executor.

The executor is self-contained (no synthorg imports) and runs inside
the sandbox. These tests cover its host-analysable pure helpers: PNG
dimension parsing and sandbox path validation.
"""

import pytest

from synthorg.tools.desktop import _executor

pytestmark = pytest.mark.unit


def _png_header(width: int, height: int) -> bytes:
    """Build a minimal PNG signature + IHDR prefix with given dimensions."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_len = (13).to_bytes(4, "big")
    ihdr_type = b"IHDR"
    return (
        signature
        + ihdr_len
        + ihdr_type
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


class TestPngDimensions:
    def test_parses_width_and_height(self) -> None:
        data = _png_header(640, 480)
        assert _executor._png_dimensions(data) == (640, 480)

    def test_square(self) -> None:
        data = _png_header(256, 256)
        assert _executor._png_dimensions(data) == (256, 256)


class TestValidatedSandboxPath:
    def test_accepts_workspace_path(self) -> None:
        resolved = _executor._validated_sandbox_path(
            "/workspace/.synthorg/desktop/screenshots/x.png",
            field="screenshot_path",
        )
        assert str(resolved).replace("\\", "/").endswith("screenshots/x.png")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _executor._validated_sandbox_path("", field="screenshot_path")

    def test_rejects_traversal(self) -> None:
        with pytest.raises(ValueError, match="must not contain"):
            _executor._validated_sandbox_path(
                "/workspace/../etc/passwd",
                field="screenshot_path",
            )

    def test_rejects_outside_root(self) -> None:
        with pytest.raises(ValueError, match="resolve under"):
            _executor._validated_sandbox_path("/etc/passwd", field="screenshot_path")

    def test_rejects_relative(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            _executor._validated_sandbox_path("rel/path.png", field="screenshot_path")


class TestDispatchTable:
    def test_all_operations_registered(self) -> None:
        assert set(_executor._DISPATCH) == {
            "launch",
            "click",
            "type",
            "key",
            "scroll",
            "screenshot",
        }
