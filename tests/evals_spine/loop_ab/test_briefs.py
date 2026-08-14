# module-kind: tests
"""The A/B brief suite must actually be able to tell right from wrong.

A brief whose checks pass regardless of what the loop produced measures nothing,
and would make the whole scoreboard meaningless while still looking healthy. So
each brief is graded twice here: once against a known-good solution, which must
score full marks, and once against a known-bad one, which must score strictly
less. Reference solutions live in this test rather than in the seed fixture, so
they are never visible to a loop under test.

These run the real subprocess grader against a really-seeded workspace, so they
also cover the workspace path end to end. That makes them integration-capability
rather than unit: each brief spawns a process per check, matching the reasoning
that puts ``test_runner_broken_scores_worse`` at the same capability for booting a
real engine. Left in the unit capability they would also starve it, since the
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

_GOOD_REPORT_PARSE: Final[str] = '''
"""Parses raw ``name: amount`` rows."""


def parse_row(raw):
    """Split one ``name: amount`` row into its parts."""
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
'''

_GOOD_REPORT_RENDER: Final[str] = '''
"""Renders the report's text."""

SEPARATOR = "-"
NAME_WIDTH = 12
AMOUNT_WIDTH = 10


def render_line(name, amount):
    """Render one parsed row as a fixed-width line."""
    return f"{name:<{NAME_WIDTH}}{amount:>{AMOUNT_WIDTH},}"


def render_header(title):
    """Render the title and its underline."""
    return f"{title}\\n{SEPARATOR * len(title)}"
'''

_GOOD_REPORT_BUILD: Final[str] = '''
"""Assembles a plain-text report from raw ``name: amount`` rows."""

from report.parse import parse_row
from report.render import render_header, render_line


def build_report(rows, *, title):
    """Build the whole report from *rows* under *title*."""
    parsed = [parse_row(row) for row in rows]
    lines = [render_header(title)]
    lines.extend(render_line(name, amount) for name, amount in parsed)
    total = sum(amount for _, amount in parsed)
    lines.append(render_line("TOTAL", total))
    return "\\n".join(lines)
'''

_GOOD_PIPELINE_STAGES: Final[str] = '''
"""The stages that ship with the package."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Double:
    """Doubles its input."""

    @property
    def name(self) -> str:
        """The name this stage is registered under."""
        return "double"

    def run(self, value: int) -> int:
        """Double *value*."""
        return value * 2


@dataclass(frozen=True)
class Increment:
    """Adds a fixed amount to its input."""

    by: int = 1

    def __post_init__(self) -> None:
        """Reject a step that would leave the value untouched."""
        if self.by == 0:
            msg = "Increment by must not be 0"
            raise ValueError(msg)

    @property
    def name(self) -> str:
        """The name this stage is registered under."""
        return "increment"

    def run(self, value: int) -> int:
        """Add ``by`` to *value*."""
        return value + self.by


@dataclass(frozen=True)
class Square:
    """Squares its input."""

    @property
    def name(self) -> str:
        """The name this stage is registered under."""
        return "square"

    def run(self, value: int) -> int:
        """Square *value*."""
        return value * value
'''

_GOOD_PIPELINE_REGISTRY: Final[str] = '''
"""Maps a stage name to the stage that answers to it."""

from pipeline.stage import Stage
from pipeline.stages import Double, Increment, Square

REGISTRY: dict[str, Stage] = {
    "double": Double(),
    "increment": Increment(),
    "square": Square(),
}


def get_stage(name: str) -> Stage:
    """Look up the stage registered under *name*."""
    stage = REGISTRY.get(name)
    if stage is None:
        msg = f"no stage named {name!r}"
        raise KeyError(msg)
    return stage
'''

_GOOD_PIPELINE_SEQUENCE: Final[str] = '''
"""Runs a named sequence of stages in order."""

from dataclasses import dataclass

from pipeline.registry import get_stage


@dataclass(frozen=True)
class Sequence:
    """The stage names to run, in order."""

    names: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse a sequence naming a stage that does not exist."""
        for name in self.names:
            get_stage(name)

    def run(self, value: int) -> int:
        """Feed *value* through each stage in order."""
        for name in self.names:
            value = get_stage(name).run(value)
        return value
'''

_GOOD_PIPELINE_INIT: Final[str] = '''
"""A small staged-transformation pipeline used by the loop A/B benchmark."""

from pipeline.registry import REGISTRY, get_stage
from pipeline.sequence import Sequence
from pipeline.stage import Stage
from pipeline.stages import Double, Increment, Square

__all__ = [
    "REGISTRY",
    "Double",
    "Increment",
    "Sequence",
    "Square",
    "Stage",
    "get_stage",
]
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
    ).project_dir
    for relative, body in files.items():
        target = work_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.lstrip("\n"), encoding="utf-8")
    assert brief.checks is not None, f"{brief.brief_id} declares no checks"
    resolved = brief.model_copy(update={"checks": resolve_checks(brief.checks)})
    return grade_executable(resolved, work_dir).score


def test_the_suite_covers_every_routing_bucket() -> None:
    """Promotion advice per complexity needs evidence at every complexity.

    ``loop_complexity_overrides`` can name a loop for EPIC, so a suite topping
    out at COMPLEX would leave that bucket routed on no measurement at all.
    """
    briefs = load_brief_suite(_SUITE)

    assert len(briefs) == 5
    # Two at COMPLEX deliberately: one bucket carried by a single brief reports
    # that brief's quirks as the bucket's verdict.
    assert sorted(b.estimated_complexity for b in briefs) == [1, 2, 3, 3, 4]
    assert all(b.kind is BriefKind.EXECUTABLE for b in briefs)
    assert all(b.workspace is not None for b in briefs)


def test_every_brief_seeds_a_real_committed_fixture(tmp_path: Path) -> None:
    """A brief pointing at a missing fixture would grade every loop at zero."""
    for brief in load_brief_suite(_SUITE):
        work_dir = seed_workspace(
            brief=brief, suite_root=_SUITE, work_root=tmp_path / brief.brief_id
        ).project_dir

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


def test_the_refactor_brief_accepts_the_split_package(tmp_path: Path) -> None:
    """The split is completable to full marks with behaviour preserved."""
    grade = _graded(
        _brief("loop-ab-refactor"),
        tmp_path,
        {
            "report/parse.py": _GOOD_REPORT_PARSE,
            "report/render.py": _GOOD_REPORT_RENDER,
            "report/build.py": _GOOD_REPORT_BUILD,
        },
    )

    assert grade == EXEC_TOTAL


def test_the_refactor_brief_fails_against_its_own_seed(tmp_path: Path) -> None:
    """The seed is the unsplit module, so the work genuinely remains."""
    grade = _graded(_brief("loop-ab-refactor"), tmp_path, {})

    assert grade < EXEC_TOTAL


def test_the_refactor_brief_rejects_a_split_that_changes_behaviour(
    tmp_path: Path,
) -> None:
    """Moving the code is not enough; the output has to survive the move.

    The whole point of this brief is that nothing observable may change, so a
    split that quietly drops the thousands separator has failed it even though
    every module now exists in the right place.
    """
    grade = _graded(
        _brief("loop-ab-refactor"),
        tmp_path,
        {
            "report/parse.py": _GOOD_REPORT_PARSE,
            "report/render.py": _GOOD_REPORT_RENDER.replace(":>{AMOUNT_WIDTH},", ""),
            "report/build.py": _GOOD_REPORT_BUILD,
        },
    )

    assert grade < EXEC_TOTAL


def test_the_refactor_brief_rejects_new_modules_beside_an_unsplit_one(
    tmp_path: Path,
) -> None:
    """Creating the modules while build.py still does the work is not a split."""
    grade = _graded(
        _brief("loop-ab-refactor"),
        tmp_path,
        {
            "report/parse.py": _GOOD_REPORT_PARSE,
            "report/render.py": _GOOD_REPORT_RENDER,
        },
    )

    assert grade < EXEC_TOTAL


def test_the_pipeline_brief_accepts_the_completed_package(tmp_path: Path) -> None:
    """The four-file change is completable to full marks."""
    grade = _graded(
        _brief("loop-ab-pipeline"),
        tmp_path,
        {
            "pipeline/stages.py": _GOOD_PIPELINE_STAGES,
            "pipeline/registry.py": _GOOD_PIPELINE_REGISTRY,
            "pipeline/sequence.py": _GOOD_PIPELINE_SEQUENCE,
            "pipeline/__init__.py": _GOOD_PIPELINE_INIT,
        },
    )

    assert grade == EXEC_TOTAL


def test_the_pipeline_brief_fails_against_its_own_seed(tmp_path: Path) -> None:
    """Neither new stage nor the composite exists in the seeded package."""
    grade = _graded(_brief("loop-ab-pipeline"), tmp_path, {})

    assert grade < EXEC_TOTAL


def test_the_pipeline_brief_rejects_stages_that_were_never_registered(
    tmp_path: Path,
) -> None:
    """Writing the stages without wiring them leaves the sequence unable to run."""
    grade = _graded(
        _brief("loop-ab-pipeline"),
        tmp_path,
        {
            "pipeline/stages.py": _GOOD_PIPELINE_STAGES,
            "pipeline/sequence.py": _GOOD_PIPELINE_SEQUENCE,
            "pipeline/__init__.py": _GOOD_PIPELINE_INIT,
        },
    )

    assert grade < EXEC_TOTAL


def test_the_pipeline_brief_rejects_validation_deferred_to_run(
    tmp_path: Path,
) -> None:
    """The spec asks for construction-time validation, and says so once.

    A Sequence that only fails when it runs is the shape a loop lands on by
    copying the surrounding style instead of reading the spec, so the brief has
    to be able to tell the two apart.
    """
    lazy = _GOOD_PIPELINE_SEQUENCE.replace(
        "    def __post_init__(self) -> None:\n"
        '        """Refuse a sequence naming a stage that does not exist."""\n'
        "        for name in self.names:\n"
        "            get_stage(name)\n\n",
        "",
    )
    grade = _graded(
        _brief("loop-ab-pipeline"),
        tmp_path,
        {
            "pipeline/stages.py": _GOOD_PIPELINE_STAGES,
            "pipeline/registry.py": _GOOD_PIPELINE_REGISTRY,
            "pipeline/sequence.py": lazy,
            "pipeline/__init__.py": _GOOD_PIPELINE_INIT,
        },
    )

    assert grade < EXEC_TOTAL
