# module-kind: tests
"""The A/B brief suite must actually be able to tell right from wrong.

A brief whose checks pass regardless of what the loop produced measures nothing,
and would make the whole scoreboard meaningless while still looking healthy. So
each brief is graded twice here: once against a known-good solution, which must
score full marks, and once against a known-bad one, which must score strictly
less. Reference solutions live in this test rather than in the seed fixture, so
they are never visible to a loop under test.

These run the real subprocess grader against a really-seeded workspace, so they
also cover the workspace path end to end. That makes them integration-tier
rather than unit: each brief spawns a process per check, matching the reasoning
that puts ``test_runner_broken_scores_worse`` at the same tier for booting a
real engine. Left in the unit tier they would also starve it, since the
subprocess load slows every other worker.
"""

from pathlib import Path
from typing import Final

import pytest

from evals.loader.briefs import load_brief_suite
from evals.loop_ab.workspace import seed_workspace
from evals.models.brief import Brief, BriefKind
from evals.runner.interpreter import resolve_checks
from evals.scoring.executable import EXEC_TOTAL, grade_executable

pytestmark = pytest.mark.integration

_SUITE: Final[Path] = (
    Path(__file__).resolve().parents[3] / "evals" / "loop_ab" / "briefs"
)

_GOOD_TEXTKIT: Final[str] = '''
import re


def slugify(value: str) -> str:
    """Lower-case, collapse non-alphanumeric runs to hyphens, strip edges."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def truncate(value: str, limit: int) -> str:
    """Return value unchanged when short enough, else ellipsis-terminate it."""
    if limit < 1:
        msg = "limit must be >= 1"
        raise ValueError(msg)
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "\\u2026"
'''

_BAD_TEXTKIT: Final[str] = '''
def slugify(value: str) -> str:
    """Lower-cases but never collapses separators."""
    return value.lower()


def truncate(value: str, limit: int) -> str:
    """Truncates without the ellipsis and without validating the limit."""
    return value[:limit]
'''

_GOOD_ACCOUNTS: Final[str] = '''
"""Account balance tracking."""

from dataclasses import dataclass, field

CENTS_PRECISION = 2


@dataclass
class Account:
    """A named account holding an ordered list of signed entries."""

    name: str
    entries: list[float] = field(default_factory=list)

    def deposit(self, amount: float) -> None:
        """Record a deposit of *amount*."""
        if amount < 0:
            msg = "deposit must be non-negative"
            raise ValueError(msg)
        self.entries.append(amount)

    def withdraw(self, amount: float) -> None:
        """Record a withdrawal of *amount*."""
        if amount < 0:
            msg = "withdrawal must be non-negative"
            raise ValueError(msg)
        if amount > self.balance:
            msg = "insufficient funds"
            raise ValueError(msg)
        self.entries.append(-amount)

    @property
    def balance(self) -> float:
        """Current balance across every recorded entry."""
        return round(sum(self.entries), CENTS_PRECISION)
'''

_GOOD_INVENTORY_MODELS: Final[str] = '''
"""Inventory domain models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """A stock-keeping unit and the quantity held of it."""

    sku: str
    quantity: int

    def __post_init__(self) -> None:
        """Reject a negative holding."""
        if self.quantity < 0:
            msg = f"quantity for {self.sku!r} must be >= 0"
            raise ValueError(msg)


@dataclass(frozen=True)
class Reservation:
    """Stock held back against a future order."""

    ref: str
    sku: str
    quantity: int

    def __post_init__(self) -> None:
        """Reject an empty reservation."""
        if self.quantity < 1:
            msg = f"reservation {self.ref!r} must reserve at least 1"
            raise ValueError(msg)
'''

_GOOD_INVENTORY_STORE: Final[str] = '''
"""In-memory inventory store."""

import itertools

from inventory.models import Item, Reservation


class Store:
    """Holds stock levels keyed by SKU, with reservations against them."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._reservations: dict[str, Reservation] = {}
        self._refs = itertools.count(1)

    def add(self, item: Item) -> None:
        """Add *item*'s quantity to the stock held for its SKU."""
        existing = self._items.get(item.sku)
        held = existing.quantity if existing is not None else 0
        self._items[item.sku] = Item(sku=item.sku, quantity=held + item.quantity)

    def quantity(self, sku: str) -> int:
        """Total quantity held for *sku*, reserved or not."""
        item = self._items.get(sku)
        return item.quantity if item is not None else 0

    def available(self, sku: str) -> int:
        """Held quantity for *sku* minus everything reserved against it."""
        reserved = sum(
            r.quantity for r in self._reservations.values() if r.sku == sku
        )
        return self.quantity(sku) - reserved

    def reserve(self, sku: str, quantity: int) -> Reservation:
        """Reserve *quantity* of *sku*."""
        if quantity > self.available(sku):
            msg = f"cannot reserve {quantity} of {sku!r}"
            raise ValueError(msg)
        reservation = Reservation(
            ref=f"r-{next(self._refs)}", sku=sku, quantity=quantity
        )
        self._reservations[reservation.ref] = reservation
        return reservation

    def release(self, ref: str) -> None:
        """Cancel the reservation with *ref*."""
        del self._reservations[ref]
'''

_GOOD_INVENTORY_INIT: Final[str] = '''
"""A small inventory store used by the loop A/B benchmark."""

from inventory.models import Item, Reservation
from inventory.store import Store

__all__ = ["Item", "Reservation", "Store"]
'''


def _brief(brief_id: str) -> Brief:
    """Load one brief from the committed A/B suite."""
    briefs = {b.brief_id: b for b in load_brief_suite(_SUITE)}
    assert brief_id in briefs, f"{brief_id} missing from {sorted(briefs)}"
    return briefs[brief_id]


def _graded(brief: Brief, tmp_path: Path, files: dict[str, str]) -> int:
    """Seed *brief*'s workspace, apply *files*, and return the grade.

    Returns:
        The score ``grade_executable`` assigns to the resulting workspace.
    """
    work_dir = seed_workspace(
        brief=brief, suite_root=_SUITE, work_root=tmp_path / "work"
    )
    for relative, body in files.items():
        target = work_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.lstrip("\n"), encoding="utf-8")
    resolved = brief.model_copy(
        update={"checks": resolve_checks(brief.checks)}  # type: ignore[union-attr]
    )
    return grade_executable(resolved, work_dir).score


def test_the_suite_covers_three_distinct_complexities() -> None:
    """Per-complexity promotion advice needs evidence at several complexities."""
    briefs = load_brief_suite(_SUITE)

    assert len(briefs) == 3
    assert {b.estimated_complexity for b in briefs} == {1, 2, 3}
    assert all(b.kind is BriefKind.EXECUTABLE for b in briefs)
    assert all(b.workspace is not None for b in briefs)


def test_every_brief_seeds_a_real_committed_fixture(tmp_path: Path) -> None:
    """A brief pointing at a missing fixture would grade every loop at zero."""
    for brief in load_brief_suite(_SUITE):
        work_dir = seed_workspace(
            brief=brief, suite_root=_SUITE, work_root=tmp_path / brief.brief_id
        )

        assert any(work_dir.iterdir()), f"{brief.brief_id} seeded an empty workspace"


def test_the_simple_brief_accepts_a_correct_solution(tmp_path: Path) -> None:
    """Full marks are actually reachable, so the ceiling is not decorative."""
    grade = _graded(_brief("loop-ab-simple"), tmp_path, {"textkit.py": _GOOD_TEXTKIT})

    assert grade == EXEC_TOTAL


def test_the_simple_brief_rejects_a_plausible_wrong_solution(tmp_path: Path) -> None:
    """A solution that imports and runs but misbehaves must not score full marks."""
    grade = _graded(_brief("loop-ab-simple"), tmp_path, {"textkit.py": _BAD_TEXTKIT})

    assert grade < EXEC_TOTAL


def test_the_simple_brief_scores_an_empty_workspace_at_zero(tmp_path: Path) -> None:
    """Doing nothing must score nothing, not pass by default."""
    grade = _graded(_brief("loop-ab-simple"), tmp_path, {})

    assert grade == 0


def test_the_bugfix_brief_accepts_the_repaired_package(tmp_path: Path) -> None:
    """The seeded defects are all fixable to full marks."""
    grade = _graded(
        _brief("loop-ab-bugfix"), tmp_path, {"ledger/accounts.py": _GOOD_ACCOUNTS}
    )

    assert grade == EXEC_TOTAL


def test_the_bugfix_brief_fails_against_its_own_seed(tmp_path: Path) -> None:
    """The seed must genuinely be broken, or the brief asks for nothing."""
    grade = _graded(_brief("loop-ab-bugfix"), tmp_path, {})

    assert grade < EXEC_TOTAL


def test_the_bugfix_brief_cannot_be_passed_by_deleting_its_tests(
    tmp_path: Path,
) -> None:
    """Acceptance is inline, so removing the shipped suite gains nothing."""
    grade = _graded(
        _brief("loop-ab-bugfix"),
        tmp_path,
        {"tests/test_accounts.py": "# removed\n"},
    )

    assert grade < EXEC_TOTAL


def test_the_feature_brief_accepts_the_implemented_feature(tmp_path: Path) -> None:
    """The multi-file change is completable to full marks."""
    grade = _graded(
        _brief("loop-ab-feature"),
        tmp_path,
        {
            "inventory/models.py": _GOOD_INVENTORY_MODELS,
            "inventory/store.py": _GOOD_INVENTORY_STORE,
            "inventory/__init__.py": _GOOD_INVENTORY_INIT,
        },
    )

    assert grade == EXEC_TOTAL


def test_the_feature_brief_fails_against_its_own_seed(tmp_path: Path) -> None:
    """The feature genuinely does not exist yet in the seeded package."""
    grade = _graded(_brief("loop-ab-feature"), tmp_path, {})

    assert grade < EXEC_TOTAL


def test_the_feature_brief_rejects_a_partial_implementation(tmp_path: Path) -> None:
    """Adding the model without wiring the store is not a completed feature."""
    grade = _graded(
        _brief("loop-ab-feature"),
        tmp_path,
        {
            "inventory/models.py": _GOOD_INVENTORY_MODELS,
            "inventory/__init__.py": _GOOD_INVENTORY_INIT,
        },
    )

    assert grade < EXEC_TOTAL
