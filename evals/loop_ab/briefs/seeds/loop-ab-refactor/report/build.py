"""Builds a plain-text report from raw ``name: amount`` rows.

Everything lives here: parsing the rows, rendering the lines, and assembling
the whole document.
"""

SEPARATOR = "-"
NAME_WIDTH = 12
AMOUNT_WIDTH = 10


def _parse_row(raw):
    """Split one ``name: amount`` row into its parts.

    Raises ValueError when the row has no colon or a non-integer amount.
    """
    name, colon, amount = raw.partition(":")
    if not colon:
        msg = f"row {raw!r} has no ':' separator"
        raise ValueError(msg)
    name = name.strip()
    if not name:
        msg = f"row {raw!r} has an empty name"
        raise ValueError(msg)
    try:
        value = int(amount.strip())
    except ValueError:
        msg = f"row {raw!r} has a non-integer amount"
        raise ValueError(msg) from None
    return name, value


def _render_line(name, amount):
    """Render one parsed row as a fixed-width line."""
    return f"{name:<{NAME_WIDTH}}{amount:>{AMOUNT_WIDTH},}"


def _render_header(title):
    """Render the title and its underline."""
    return f"{title}\n{SEPARATOR * len(title)}"


def build_report(rows, *, title):
    """Build the whole report from *rows* under *title*.

    Rows are rendered in the order given, and a total line is appended.
    """
    parsed = [_parse_row(row) for row in rows]
    lines = [_render_header(title)]
    lines.extend(_render_line(name, amount) for name, amount in parsed)
    total = sum(amount for _, amount in parsed)
    lines.append(_render_line("TOTAL", total))
    return "\n".join(lines)
