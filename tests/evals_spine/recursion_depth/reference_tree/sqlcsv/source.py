# module-kind: tests
"""Read a CSV file into typed rows."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sqlcsv.errors import InputError, NotFoundError

Value = str | int | float | None
Row = dict[str, Value]

#: An optional sign and digits, and nothing else.
_INT_LITERAL = re.compile(r"[+-]?[0-9]+")

#: The decimal grammar, with an optional exponent. Deliberately excludes the
#: special values `float()` accepts by name.
_FLOAT_LITERAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


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
    # Matched against the directory listing rather than by opening the path.
    # R06 makes identifiers case-sensitive, and `path.is_file()` delegates that
    # to the filesystem, which answers differently on Windows and Linux: the
    # same query then resolves `Orders` to `orders` on one and fails on the
    # other, so the delivery's conformance would depend on where it ran.
    if path.name not in {entry.name for entry in data_dir.iterdir()}:
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

    Matched against the grammar rather than handed to ``int()``, which also
    accepts underscore separators and surrounding whitespace and would then
    store a value differing from the CSV text it came from.

    Returns:
        Whether it is an integer literal.
    """
    return _INT_LITERAL.fullmatch(cell) is not None


def _is_float(cell: str) -> bool:
    """Whether *cell* is a decimal literal.

    Matched rather than handed to ``float()``, which accepts ``nan``, ``inf``
    and ``-Infinity``. A text column whose cells read "nan" would otherwise be
    promoted to float, and NaN then makes equality and de-duplication behave
    unpredictably, because it compares equal to nothing including itself.

    Returns:
        Whether it is a decimal literal.
    """
    return _FLOAT_LITERAL.fullmatch(cell) is not None
