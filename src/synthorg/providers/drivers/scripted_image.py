# module-kind: code
"""Deterministic offline image rendering for the scripted driver.

Renders a real, valid PNG whose pixels derive from a stable hash of the
prompt + size + index, so the ``example-provider`` image model exercises
the full image-generation path (provider -> tool -> asset store) with no
network and no vendor, and tests can assert byte-stable output.
"""

import hashlib
from io import BytesIO
from typing import Final

from PIL import Image, ImageDraw

_GRID: Final[int] = 8
"""Blocks per axis in the rendered mosaic."""

_CHANNELS_PER_CELL: Final[int] = 3
"""RGB channels consumed from the seed digest per mosaic cell."""


def parse_size(size: str) -> tuple[int, int]:
    """Parse a ``"<width>x<height>"`` size string into a pixel pair.

    Returns:
        A ``(width, height)`` pixel tuple.
    """
    width_s, height_s = size.split("x")
    return int(width_s), int(height_s)


def render_deterministic_png(prompt: str, *, size: str, index: int = 0) -> bytes:
    """Render a deterministic PNG mosaic seeded by the prompt.

    Args:
        prompt: The image prompt (seeds the colours).
        size: Output size as ``"<width>x<height>"``.
        index: Image index within a multi-image request (varies the seed).

    Returns:
        Valid PNG bytes.
    """
    width, height = parse_size(size)
    seed = hashlib.sha256(f"{prompt}|{size}|{index}".encode()).digest()
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    cell_w = max(1, width // _GRID)
    cell_h = max(1, height // _GRID)
    for grid_y in range(_GRID):
        for grid_x in range(_GRID):
            base = ((grid_y * _GRID + grid_x) * _CHANNELS_PER_CELL) % len(seed)
            colour = (
                seed[base],
                seed[(base + 1) % len(seed)],
                seed[(base + 2) % len(seed)],
            )
            x0 = grid_x * cell_w
            y0 = grid_y * cell_h
            draw.rectangle((x0, y0, x0 + cell_w, y0 + cell_h), fill=colour)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
