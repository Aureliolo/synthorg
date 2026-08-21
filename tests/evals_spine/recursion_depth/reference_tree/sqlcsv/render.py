# module-kind: tests
"""Render a result set."""

import csv
import io
import json

from sqlcsv.engine import Result
from sqlcsv.source import Value


def render(result: Result, fmt: str) -> str:
    """Render *result* in *fmt*.

    Args:
        result: What the statement produced.
        fmt: One of ``table``, ``csv`` or ``json``.

    Returns:
        The rendered text, newline-terminated where the format has lines.
    """
    if fmt == "csv":
        return _render_csv(result)
    if fmt == "json":
        return _render_json(result)
    return _render_table(result)


def _render_table(result: Result) -> str:
    """Render the padded table form.

    Returns:
        Header, separator rule and one line per row.
    """
    cells = [
        [_as_text(row.get(column)) for column in result.columns] for row in result.rows
    ]
    widths = [
        max(len(column), *(len(row[index]) for row in cells)) if cells else len(column)
        for index, column in enumerate(result.columns)
    ]
    header = " | ".join(
        column.ljust(width)
        for column, width in zip(result.columns, widths, strict=True)
    ).rstrip()
    rule = "-" * max(
        len(header),
        sum(widths) + 3 * max(len(widths) - 1, 0),
    )
    lines = [header, rule]
    lines.extend(
        " | ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in cells
    )
    return "\n".join(lines) + "\n"


def _render_csv(result: Result) -> str:
    """Render RFC 4180 CSV with a header.

    Returns:
        The CSV text.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow([_as_text(row.get(column)) for column in result.columns])
    return buffer.getvalue()


def _render_json(result: Result) -> str:
    """Render a JSON array of row objects.

    Returns:
        The JSON text.
    """
    payload = [
        {column: row.get(column) for column in result.columns} for row in result.rows
    ]
    return json.dumps(payload) + "\n"


def _as_text(value: Value) -> str:
    """Render one value for a text format.

    Returns:
        The value's text, empty for NULL.
    """
    if value is None:
        return ""
    return str(value)
