"""Named constants for the headless browser tool.

No magic numbers, no inline string literals: every timeout, viewport,
threshold, and path lives here so the gate
``scripts/check_no_magic_numbers.py`` stays clean and operators see
one canonical place when tuning runtime behaviour.
"""

from pathlib import Path
from typing import Final, Literal

BROWSER_LAUNCH_TIMEOUT_SECONDS: Final[float] = 30.0
NAVIGATION_TIMEOUT_SECONDS: Final[float] = 60.0
SCREENSHOT_TIMEOUT_SECONDS: Final[float] = 30.0
ACCESSIBILITY_SCAN_TIMEOUT_SECONDS: Final[float] = 45.0
START_COMMAND_TIMEOUT_SECONDS_DEFAULT: Final[float] = 30.0
START_COMMAND_READY_POLL_SECONDS: Final[float] = 0.5

DIFF_SSIM_TOLERANCE_DEFAULT: Final[float] = 0.98
DIFF_SSIM_TOLERANCE_MIN: Final[float] = 0.5
DIFF_SSIM_TOLERANCE_MAX: Final[float] = 1.0
SSIM_DATA_RANGE: Final[int] = 255

DEFAULT_VIEWPORT_WIDTH: Final[int] = 1280
DEFAULT_VIEWPORT_HEIGHT: Final[int] = 720
MIN_VIEWPORT_DIMENSION: Final[int] = 320
MAX_VIEWPORT_DIMENSION: Final[int] = 4096

MILLISECONDS_PER_SECOND: Final[int] = 1000

CHROMIUM_LAUNCH_ARGS: Final[tuple[str, ...]] = (
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--no-sandbox",
)

A11Y_IMPACT_LEVELS: Final[tuple[str, ...]] = (
    "minor",
    "moderate",
    "serious",
    "critical",
)
A11Y_MIN_IMPACT_DEFAULT: Final[Literal["minor", "moderate", "serious", "critical"]] = (
    "serious"
)
A11Y_IMPACT_RANK: Final[dict[str, int]] = {
    level: index for index, level in enumerate(A11Y_IMPACT_LEVELS)
}

SCREENSHOTS_SUBDIR: Final[str] = ".synthorg/screenshots"
BASELINE_META_FILENAME: Final[str] = ".meta.json"
DIFF_HEATMAP_SUFFIX: Final[str] = "_diff.png"

BROWSER_IMAGE_PIN_DEFAULT: Final[str] = (
    "mcr.microsoft.com/playwright/python:v1.60.0-jammy"
)
CONTAINER_WORKSPACE_ROOT: Final[str] = "/workspace"

_VENDOR_DIR: Final[Path] = Path(__file__).resolve().parent / "_vendor" / "axe-core"
AXE_BUNDLE_PATH: Final[Path] = _VENDOR_DIR / "axe.min.js"
AXE_VERSION_PIN: Final[str] = "4.10.2"
AXE_SCRIPT_MAX_BYTES: Final[int] = 5 * 1024 * 1024
SHA256_HEX_LENGTH: Final[int] = 64
SHA256_HEX_PATTERN: Final[str] = "^[a-f0-9]{64}$"

WAIT_CONDITIONS: Final[tuple[str, ...]] = (
    "load",
    "domcontentloaded",
    "networkidle",
)
WAIT_CONDITION_DEFAULT: Final[str] = "load"
