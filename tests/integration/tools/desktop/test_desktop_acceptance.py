"""Acceptance test for the virtual desktop tool's execution path.

Canonical acceptance: an agent operates a GUI application on the
virtual desktop. This test wires the real ``DesktopTool`` against a
real ``DockerSandbox`` running the pinned desktop image, launches a Tk
GUI fixture under Xvfb, drives keyboard input through xdotool, and
captures screenshots through scrot, asserting that real, non-empty PNGs
land in the workspace.

The complementary ``tests/integration/security/visionverify`` suite
covers the vision verifier flagging a deliverable-vs-brief mismatch with
synthetic images; this test covers the desktop-EXECUTION side that the
vision-gate test substitutes with PIL images, so the two together
exercise the full operate-then-verify acceptance.

Image-gated like the headless-browser acceptance test: it skips unless
the desktop image is built and Docker is reachable (build via
``docker/desktop/Dockerfile``).
"""

import asyncio
from pathlib import Path
from typing import Any, Final, cast

import pytest

from synthorg.tools.desktop import DesktopTool
from synthorg.tools.desktop._constants import DESKTOP_IMAGE_PIN_DEFAULT
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox

pytestmark = [
    pytest.mark.integration,
    # 300s budget: desktop image pull plus Xvfb / fluxbox cold-start plus
    # a Tk app launch plus two scrot captures. The hot-cache path
    # completes in well under a minute.
    pytest.mark.timeout(300),
]

_DESKTOP_IMAGE: Final[str] = DESKTOP_IMAGE_PIN_DEFAULT
_SANDBOX_TIMEOUT_SECONDS: Final[int] = 180
_SCREENSHOT_SUBDIR: Final[tuple[str, ...]] = (".synthorg", "desktop", "screenshots")

_FIXTURE_APP_PY: Final[str] = """import tkinter as tk

root = tk.Tk()
root.title("SynthOrg Desktop Fixture")
root.geometry("480x320")
label = tk.Label(root, text="Ready", font=("Arial", 28))
label.pack(expand=True)
root.mainloop()
"""


def _docker_and_desktop_image_available() -> bool:
    """Return True when Docker is reachable and the desktop image present."""
    try:
        import aiodocker

        async def _check() -> bool:
            client = None
            try:
                client = aiodocker.Docker()
                await client.version()
                await client.images.inspect(_DESKTOP_IMAGE)
            except Exception:
                return False
            else:
                return True
            finally:
                if client is not None:
                    await client.close()

        return asyncio.run(_check())
    except Exception:
        return False


skip_no_desktop = pytest.mark.skipif(
    not _docker_and_desktop_image_available(),
    reason=(
        "Docker daemon not available or "
        f"{_DESKTOP_IMAGE} not built (build via docker/desktop/Dockerfile)."
    ),
)


def _host_screenshot_path(workspace: Path, name: str) -> Path:
    """Resolve the host-side path a workspace screenshot lands at."""
    return workspace.joinpath(*_SCREENSHOT_SUBDIR, f"{name}.png")


def _assert_real_capture(workspace: Path, result: Any, name: str) -> None:
    """Assert a screenshot result describes a real, non-empty PNG on disk."""
    assert result.is_error is False, f"screenshot failed: {result.content!r}"
    meta = cast("dict[str, Any]", result.metadata)
    assert str(meta["saved_path"]).endswith(f"{name}.png")
    assert int(meta["width"]) > 0
    assert int(meta["height"]) > 0
    assert int(meta["file_size_bytes"]) > 0
    host_png = _host_screenshot_path(workspace, name)
    assert host_png.is_file(), f"screenshot not written to workspace: {host_png}"
    assert host_png.stat().st_size > 0, "screenshot PNG is empty"


@skip_no_desktop
async def test_desktop_launches_app_and_captures_real_screenshots(
    tmp_path: Path,
) -> None:
    """Drive a Tk app on the virtual desktop and capture real screenshots."""
    (tmp_path / "app.py").write_text(_FIXTURE_APP_PY, encoding="utf-8")

    sandbox = DockerSandbox(
        config=DockerSandboxConfig(
            image=_DESKTOP_IMAGE,
            timeout_seconds=_SANDBOX_TIMEOUT_SECONDS,
        ),
        workspace=tmp_path,
    )

    try:
        tool = DesktopTool(sandbox=sandbox, workspace=tmp_path)

        launch = await tool.execute(
            arguments={
                "mode": "launch",
                "app_command": "python3 /workspace/app.py",
            },
        )
        assert launch.is_error is False, f"launch failed: {launch.content!r}"
        launch_meta = cast("dict[str, Any]", launch.metadata)
        assert int(launch_meta["pid"]) > 0
        assert launch_meta["display"]

        first = await tool.execute(
            arguments={"mode": "screenshot", "screenshot_name": "ready"},
        )
        _assert_real_capture(tmp_path, first, "ready")

        # Inject keyboard input through xdotool to prove the input path,
        # then capture again: the second shot must also be a real PNG.
        keyed = await tool.execute(arguments={"mode": "key", "keys": "Tab"})
        assert keyed.is_error is False, f"key injection failed: {keyed.content!r}"

        second = await tool.execute(
            arguments={"mode": "screenshot", "screenshot_name": "after_key"},
        )
        _assert_real_capture(tmp_path, second, "after_key")
    finally:
        await sandbox.cleanup()
