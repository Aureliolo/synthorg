"""Event-name constants for the headless browser tool.

Centralised so structured logs use stable identifiers; never inline
literal event strings in tool code.
"""

from typing import Final

BROWSER_LAUNCH_START: Final[str] = "browser.launch.start"
BROWSER_LAUNCH_SUCCESS: Final[str] = "browser.launch.success"
BROWSER_LAUNCH_FAILED: Final[str] = "browser.launch.failed"

BROWSER_CLOSE_START: Final[str] = "browser.close.start"
BROWSER_CLOSE_FAILED: Final[str] = "browser.close.failed"

BROWSER_NAVIGATE_START: Final[str] = "browser.navigate.start"
BROWSER_NAVIGATE_SUCCESS: Final[str] = "browser.navigate.success"
BROWSER_NAVIGATE_TIMEOUT: Final[str] = "browser.navigate.timeout"
BROWSER_NAVIGATE_FAILED: Final[str] = "browser.navigate.failed"

BROWSER_SCREENSHOT_START: Final[str] = "browser.screenshot.start"
BROWSER_SCREENSHOT_SUCCESS: Final[str] = "browser.screenshot.success"
BROWSER_SCREENSHOT_FAILED: Final[str] = "browser.screenshot.failed"

BROWSER_A11Y_SCAN_START: Final[str] = "browser.a11y.scan.start"
BROWSER_A11Y_SCAN_SUCCESS: Final[str] = "browser.a11y.scan.success"
BROWSER_A11Y_VIOLATIONS_FOUND: Final[str] = "browser.a11y.violations_found"
BROWSER_A11Y_SCAN_FAILED: Final[str] = "browser.a11y.scan.failed"

BROWSER_DIFF_START: Final[str] = "browser.diff.start"
BROWSER_DIFF_SUCCESS: Final[str] = "browser.diff.success"
BROWSER_DIFF_FAILED: Final[str] = "browser.diff.failed"

BROWSER_BASELINE_CREATED: Final[str] = "browser.baseline.created"
BROWSER_BASELINE_NOT_FOUND: Final[str] = "browser.baseline.not_found"
BROWSER_BASELINE_WRITE_FAILED: Final[str] = "browser.baseline.write_failed"
BROWSER_BASELINE_SIDECAR_WRITTEN: Final[str] = "browser.baseline.sidecar_written"
BROWSER_ARGS_VALIDATION_FAILED: Final[str] = "browser.args.validation_failed"
BROWSER_EXECUTOR_FAILED: Final[str] = "browser.executor.failed"
BROWSER_ASSETS_DEPLOYED: Final[str] = "browser.assets.deployed"

BROWSER_SPEC_START: Final[str] = "browser.spec.start"
BROWSER_SPEC_SUCCESS: Final[str] = "browser.spec.success"
BROWSER_SPEC_FAILED: Final[str] = "browser.spec.failed"

BROWSER_START_COMMAND_START: Final[str] = "browser.start_command.start"
BROWSER_START_COMMAND_SUCCESS: Final[str] = "browser.start_command.success"
BROWSER_START_COMMAND_FAILED: Final[str] = "browser.start_command.failed"
