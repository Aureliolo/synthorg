# module-kind: tests
"""Read a CSV file into typed rows."""

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlcsv.errors import InputError, NotFoundError

Value = str | int | float | None
Row = dict[str, Value]


@dataclass(frozen=True)
class Table:
    """One loaded CSV file.

    Attributes:
        name: The table name, which is the file's stem.
        columns: The header, in declaration order.
        rows: The typed data rows.
    """

    name: str
    columns: tuple[str, ...]
    rows: tuple[Row, ...]


def load_table(data_dir: Path, name: str) -> Table:
    """Load ``<name>.csv`` from *data_dir*.

    Args:
        data_dir: The directory tables are resolved in.
        name: The table name.

    Returns:
        The loaded table.

    Raises:
        InputError: The data directory is not readable.
        NotFoundError: No file backs the named table.
    """
    if not data_dir.is_dir():
        msg = f"data directory {str(data_dir)!r} does not exist"
        raise InputError(msg)
    path = data_dir / f"{name}.csv"
    if not path.is_file():
        msg = f"no table named {name!r} in {str(data_dir)!r}"
        raise NotFoundError(msg)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return Table(name=name, columns=(), rows=())
        raw = [list(record) for record in reader]
    columns = tuple(header)
    typed = _typed_columns(columns, raw)
    rows = tuple(
        {column: typed[column][index] for column in columns}
        for index in range(len(raw))
    )
    return Table(name=name, columns=columns, rows=rows)


def _typed_columns(
    columns: tuple[str, ...], raw: list[list[str]]
) -> dict[str, list[Value]]:
    """Decide each column's type from every value it holds.

    A column is numeric only when every non-empty value parses, so one stray
    label demotes the whole column to text rather than leaving the rows
    disagreeing about what they hold.

    Returns:
        The typed values, keyed by column.
    """
    typed: dict[str, list[Value]] = {}
    for position, column in enumerate(columns):
        cells = [record[position] if position < len(record) else "" for record in raw]
        typed[column] = _convert(cells)
    return typed


def _convert(cells: list[str]) -> list[Value]:
    """Convert one column's cells to the narrowest type they all admit.

    Returns:
        The converted values, with empty cells as ``None``.
    """
    present = [cell for cell in cells if cell != ""]
    if present and all(_is_int(cell) for cell in present):
        return [None if cell == "" else int(cell) for cell in cells]
    if present and all(_is_float(cell) for cell in present):
        return [None if cell == "" else float(cell) for cell in cells]
    return [None if cell == "" else cell for cell in cells]


def _is_int(cell: str) -> bool:
    """Whether *cell* is an integer literal.

    Returns:
        Whether it parses as an int.
    """
    try:
        int(cell)
    except ValueError:
        return False
    return True


def _is_float(cell: str) -> bool:
    """Whether *cell* is a decimal literal.

    Returns:
        Whether it parses as a float.
    """
    try:
        float(cell)
    except ValueError:
        return False
    return True
