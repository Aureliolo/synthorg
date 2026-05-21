"""Event-name constants for the virtual desktop tool.

Centralised so structured logs use stable identifiers; never inline
literal event strings in tool code.
"""

from typing import Final

DESKTOP_SESSION_START: Final[str] = "desktop.session.start"
DESKTOP_SESSION_READY: Final[str] = "desktop.session.ready"
DESKTOP_SESSION_FAILED: Final[str] = "desktop.session.failed"

DESKTOP_LAUNCH_START: Final[str] = "desktop.launch.start"
DESKTOP_LAUNCH_SUCCESS: Final[str] = "desktop.launch.success"
DESKTOP_LAUNCH_FAILED: Final[str] = "desktop.launch.failed"

DESKTOP_INPUT_START: Final[str] = "desktop.input.start"
DESKTOP_INPUT_SUCCESS: Final[str] = "desktop.input.success"
DESKTOP_INPUT_FAILED: Final[str] = "desktop.input.failed"

DESKTOP_SCREENSHOT_START: Final[str] = "desktop.screenshot.start"
DESKTOP_SCREENSHOT_SUCCESS: Final[str] = "desktop.screenshot.success"
DESKTOP_SCREENSHOT_FAILED: Final[str] = "desktop.screenshot.failed"

DESKTOP_ARGS_VALIDATION_FAILED: Final[str] = "desktop.args.validation_failed"
DESKTOP_EXECUTOR_FAILED: Final[str] = "desktop.executor.failed"
DESKTOP_ASSETS_DEPLOYED: Final[str] = "desktop.assets.deployed"
DESKTOP_CLOSE_FAILED: Final[str] = "desktop.close.failed"
