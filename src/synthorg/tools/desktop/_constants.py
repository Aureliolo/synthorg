"""Named constants for the virtual desktop tool.

No magic numbers, no inline string literals: every timeout, geometry,
and path lives here so the gate ``scripts/check_no_magic_numbers.py``
stays clean and operators see one canonical place when tuning runtime
behaviour.
"""

from typing import Final

from synthorg import __version__

SESSION_START_TIMEOUT_SECONDS: Final[float] = 30.0
LAUNCH_TIMEOUT_SECONDS: Final[float] = 30.0
MAX_LAUNCH_TIMEOUT_MULTIPLIER: Final[int] = 20
INPUT_TIMEOUT_SECONDS: Final[float] = 15.0
SCREENSHOT_TIMEOUT_SECONDS: Final[float] = 20.0
OUTER_TIMEOUT_BUFFER_SECONDS: Final[float] = 20.0

DEFAULT_DISPLAY: Final[str] = ":99"
DEFAULT_SCREEN_WIDTH: Final[int] = 1280
DEFAULT_SCREEN_HEIGHT: Final[int] = 800
DEFAULT_COLOR_DEPTH: Final[int] = 24
MIN_SCREEN_DIMENSION: Final[int] = 320
MAX_SCREEN_DIMENSION: Final[int] = 4096

DEFAULT_VNC_PORT: Final[int] = 5900
MIN_VNC_PORT: Final[int] = 1024
MAX_VNC_PORT: Final[int] = 65535

DEFAULT_CLICK_BUTTON: Final[int] = 1
MIN_MOUSE_BUTTON: Final[int] = 1
MAX_MOUSE_BUTTON: Final[int] = 5
MIN_COORDINATE: Final[int] = 0
MAX_COORDINATE: Final[int] = MAX_SCREEN_DIMENSION

DEFAULT_SCROLL_AMOUNT: Final[int] = 3
MIN_SCROLL_AMOUNT: Final[int] = 1
MAX_SCROLL_AMOUNT: Final[int] = 100

SETTLE_DELAY_SECONDS: Final[float] = 0.2
MAX_SETTLE_DELAY_SECONDS: Final[float] = 10.0

CONTAINER_WORKSPACE_ROOT: Final[str] = "/workspace"
SCREENSHOTS_SUBDIR: Final[str] = ".synthorg/desktop/screenshots"

DESKTOP_IMAGE_PIN_DEFAULT: Final[str] = (
    f"ghcr.io/aureliolo/synthorg-desktop:v{__version__}"
)

SHA256_HEX_LENGTH: Final[int] = 64
SHA256_HEX_PATTERN: Final[str] = "^[a-f0-9]{64}$"
PNG_EXTENSION: Final[str] = ".png"
