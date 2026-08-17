"""Every enum member a column claims to hold must survive a write to it.

A ``CHECK (col IN (...))`` is a second copy of an enum, written in SQL, once per
backend. Nothing made the copies agree with the original, and they drifted:
``BlockedReason.NO_CAPABLE_AGENT`` was added to the enum, the public DTO and
three writers while both ``schema.sql`` files kept listing four values, so every
one of those parks raised on insert and the task never reached BLOCKED at all.

The conformance tier catches this too, but only against a live backend, so it
runs in CI on the pushed branch. These read the declared DDL directly, which
costs nothing and fails in the fast tier where the divergence is introduced.
"""

import re
from enum import StrEnum
from pathlib import Path
from typing import Final

import pytest

from synthorg.core.task_enums import BlockedReason

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCHEMAS: Final[dict[str, Path]] = {
    "sqlite": _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "schema.sql",
    "postgres": (
        _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "schema.sql"
    ),
}

#: Columns whose CHECK list must be exactly the members of an enum, and the enum
#: it mirrors. A new enum-backed CHECK column belongs here in the same commit.
_ENUM_CHECKED_COLUMNS: Final[tuple[tuple[str, type[StrEnum]], ...]] = (
    ("blocked_reason", BlockedReason),
)


def _checked_values(ddl: str, column: str) -> set[str]:
    """The quoted values a ``CHECK (<column> IN (...))`` admits.

    Returns:
        The admitted values, or an empty set when the column has no such CHECK.
    """
    match = re.search(
        rf"{re.escape(column)}\s+IN\s*\((?P<values>[^)]*)\)",
        ddl,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return set()
    return set(re.findall(r"'([^']*)'", match.group("values")))


@pytest.mark.parametrize("backend", sorted(_SCHEMAS))
@pytest.mark.parametrize(("column", "enum"), _ENUM_CHECKED_COLUMNS)
def test_check_constraint_admits_every_enum_member(
    backend: str, column: str, enum: type[StrEnum]
) -> None:
    """The SQL copy of an enum admits exactly what the enum declares."""
    ddl = _SCHEMAS[backend].read_text(encoding="utf-8")
    admitted = _checked_values(ddl, column)

    assert admitted, f"{backend}: no CHECK ... IN (...) found for {column}"
    # Both directions: a missing member is a write that raises, and a surplus
    # value is a row the domain model would then refuse to parse back.
    assert admitted == {member.value for member in enum}
