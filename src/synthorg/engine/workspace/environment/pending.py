# module-kind: code
"""Classify a pending criterion test from a machine-readable run report.

A skeleton commits one test per acceptance criterion before anything implements
the contract, so those tests fail. A suite that plainly fails would break the
trunk invariant the whole loop rests on, which is why a pending test failing its
declared assertion reads as green.

The narrowness is what stops that becoming a mute button. **Only the declared
failure is green.** Every other way a test can end stays red, and each for its
own reason: a collection error means the skeleton does not import, an unexpected
exception means it is wrong rather than absent, and a timeout or a runner crash
means nothing was measured at all. Reading any of those as green would let a
skeleton that does not even load ship as a green trunk.

It is strict in the other direction too. A pending test that **passes** is red
until the same commit clears its entry, so a unit cannot satisfy its contract
and leave the marker behind for the next unit to inherit. Clearing the entry is
the mechanical signal that the unit is done.

An exit status cannot express any of this: it says a run failed, never why one
test did. The report is what carries the distinction, so a report that is
missing, unreadable or malformed classifies every pending criterion red rather
than falling back to the status. That is the fail-closed direction: the cost of
being wrong is a rework round, against shipping a skeleton nobody ran.

The JUnit shape is what every runner this product meets already emits, but its
two failure elements do not draw the line this module needs. A runner picks
between them by PHASE, not by what was raised: pytest writes ``error`` for a
collection, setup or teardown failure and ``failure`` for everything that
reaches the test body, so an unexpected exception inside a pending test is
recorded exactly as a lost assertion is. The tag answers "did the test run at
all"; what it RAISED is what answers "did it assert", read from the class the
runner named and only from free-form text when it named none.

Reading the report at all is a separate question with a separate threat model,
and lives in :mod:`synthorg.engine.workspace.environment.pending_report`.
"""

import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple
from xml.etree.ElementTree import Element

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.manifest import PendingTest
from synthorg.engine.workspace.environment.pending_report import read_report
from synthorg.observability import get_logger

logger = get_logger(__name__)

#: The JUnit child element a runner writes when an assertion failed. This is the
#: one outcome a pending test is allowed to reach.
_FAILURE_TAG: Final[str] = "failure"

#: The JUnit child element a runner writes when the test raised outside its own
#: body: a collection error, or a failure in setup or teardown.
_ERROR_TAG: Final[str] = "error"

#: Markers identifying a ``failure`` as a genuine assertion rather than an
#: unrelated exception recorded under the same tag.
#:
#: The tag alone cannot answer this. pytest chooses between the two by PHASE and
#: not by what was raised (``_pytest/junitxml.py``: a call-phase failure is
#: ``failure`` whatever the exception, and only setup, teardown and collection
#: reach ``error``), so a pending test whose body raises ``KeyError`` is
#: recorded identically to one whose assertion failed. Reading every ``failure``
#: as the declared outcome therefore forgives a skeleton that crashes, which is
#: the one thing the pending marker is not allowed to cover.
#:
#: The FALLBACK, for a runner that names no exception class at all: a jest or
#: vitest message is prose, so free-form text is the only signal there is. Where
#: a class IS named it wins outright, because a substring test reads
#: ``ValueError: cannot assert on empty input`` as a declared failure.
#:
#: Matched case-insensitively anywhere in the message, and deliberately a small
#: declared vocabulary rather than a denylist of exception types, which is
#: unbounded. An unrecognised message reads RED: a pending test the runner
#: cannot be shown to have asserted is exactly the case the operator should see.
_ASSERTION_MARKERS: Final[tuple[str, ...]] = (
    "assert",
    "expect(",
)

#: What an assertion's exception class is called, matched as a SUFFIX so a
#: framework subclass of it counts too.
_ASSERTION_TYPE_SUFFIX: Final[str] = "assertionerror"

#: A dotted exception class name, used to read the class off a message that
#: opens with one. Anchored to the whole candidate so ordinary prose before a
#: colon ("cannot open file: no such file") is not mistaken for a class.
_TYPE_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

#: The JUnit child element a runner writes when the test did not run. A pending
#: test that was skipped measured nothing, so it cannot be evidence that the
#: contract is merely unimplemented.
_SKIPPED_TAG: Final[str] = "skipped"


class PendingVerdict(StrEnum):
    """Whether a pending criterion's test may be read as green.

    ``GREEN`` is the declared failure and nothing else. ``RED`` is every other
    outcome, including the outcomes that mean nothing was measured.
    """

    GREEN = "green"
    RED = "red"


class CriterionOutcome(BaseModel):
    """One pending criterion's verdict, with the reason it got that verdict.

    The reason is not decoration: a red pending test is routed back to an author
    who has to be told whether their contract failed to import or their test
    unexpectedly passed, and those two demand opposite fixes.

    Attributes:
        criterion: The normalised criterion key from the manifest.
        test_id: The test the manifest named for that criterion.
        verdict: Green only for the declared assertion failure.
        reason: Why, in words the author can act on.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr
    test_id: NotBlankStr
    verdict: PendingVerdict
    reason: NotBlankStr


class PendingReport(BaseModel):
    """Every pending criterion's outcome for one run.

    Attributes:
        outcomes: One entry per declared pending criterion, in manifest order.
        report_read: Whether the run's report could be read and parsed at all.
            False means every outcome below is red for that reason alone, which
            a caller should say out loud rather than reporting each criterion as
            if it had been measured and lost.
        other_failures: How many cases OUTSIDE the pending set failed or
            errored. Counted because a suite whose pending tests are all
            correctly failing exits non-zero by construction, so the exit
            status can no longer tell a caller whether anything real broke.
            Reading the pending verdict alone would then pass a run that also
            broke three ordinary tests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    outcomes: tuple[CriterionOutcome, ...] = ()
    report_read: bool = True
    other_failures: int = Field(default=0, ge=0)

    @property
    def green(self) -> bool:
        """Whether the run is clean given what it declared pending.

        Returns:
            ``True`` when every pending criterion reached its declared failure
            and nothing outside the pending set broke. An empty pending set is
            green: a skeleton that declared no pending criteria has nothing
            outstanding, which is a different claim from one whose report went
            missing.
        """
        return (
            self.report_read
            and self.other_failures == 0
            and all(
                outcome.verdict is PendingVerdict.GREEN for outcome in self.outcomes
            )
        )


class _CaseOutcome(NamedTuple):
    """What a runner recorded against one test case.

    The message and the raised type both travel with the tag because the tag
    alone does not separate an assertion from an unrelated exception; see
    :data:`_ASSERTION_MARKERS`.
    """

    #: The outcome element's tag, or ``None`` for a pass.
    tag: str | None
    #: The outcome's ``message`` attribute, empty when it carries none.
    message: str
    #: The outcome's ``type`` attribute, empty when it carries none.
    raised: str = ""


def _outcome_of(case: Element) -> _CaseOutcome:
    """Return the JUnit outcome recorded inside *case*.

    Returns:
        The first recognised outcome child's tag, message and raised type, or a
        tag of ``None`` when the case carries none, which is how a runner
        records a pass.
    """
    for tag in (_ERROR_TAG, _FAILURE_TAG, _SKIPPED_TAG):
        element = case.find(tag)
        if element is not None:
            return _CaseOutcome(
                tag=tag,
                message=element.get("message", ""),
                raised=element.get("type", ""),
            )
    return _CaseOutcome(tag=None, message="")


def _spellings(case: Element) -> tuple[str, ...]:
    """Every node id a manifest could legitimately name *case* by.

    Returns:
        The bare name plus, when the case carries one, both classname-qualified
        forms; empty when the case has no name at all.
    """
    name = case.get("name", "")
    if not name:
        return ()
    classname = case.get("classname", "")
    node_id = _node_id(case, classname=classname, name=name)
    if not classname:
        return (name, *node_id)
    return (name, f"{classname}::{name}", f"{classname}.{name}", *node_id)


def _node_id(case: Element, *, classname: str, name: str) -> tuple[str, ...]:
    """Rebuild the runner's own node id for *case*, when it can be.

    This is the spelling a manifest is documented to carry, and none of the
    classname-derived forms can produce it: pytest writes ``classname`` as the
    DOTTED module path with no suffix (``tests.test_score``) and keeps the file
    only in ``file``, so the node id ``tests/test_score.py::test_a`` shares no
    substring boundary with any of them. A manifest naming a test the way its
    runner names it would otherwise match nothing at all, and every pending
    criterion would read as a test the report does not contain.

    Rebuilt rather than read whole because JUnit has no node-id attribute. The
    class segment is what ``classname`` carries beyond the module the file
    names, so a method test keeps its ``::TestCase::`` hop and a plain function
    test does not grow one.

    Returns:
        The node id as a one-tuple, or empty when the case names no file.
    """
    file = case.get("file", "")
    if not file:
        return ()
    module = file.removesuffix(".py").replace("\\", "/").replace("/", ".")
    segments = classname.removeprefix(module).strip(".")
    return (f"{file}::{segments}::{name}",) if segments else (f"{file}::{name}",)


def _count_other_failures(
    root: Element, pending_ids: frozenset[str], ambiguous: frozenset[str]
) -> int:
    """Count failing cases the manifest did not declare pending.

    Counted from the cases rather than from the index, because the index holds
    each case under three spellings and would treble every failure.

    A spelling two cases share excuses neither: it names both, so "this failure
    is the declared one" is exactly what cannot be established, and letting it
    excuse them hides an unrelated break behind a pending marker. The criterion
    that named it is red on the same reasoning, so counting these costs a run
    that was already being refused.

    Returns:
        How many non-pending cases failed or errored. A skip is not one: a
        skipped ordinary test is a decision the suite made, not a break.
    """
    return sum(
        1
        for case in root.iter("testcase")
        if (spellings := _spellings(case))
        and not pending_ids.intersection(set(spellings) - ambiguous)
        and _outcome_of(case).tag in {_FAILURE_TAG, _ERROR_TAG}
    )


class _CaseIndex(NamedTuple):
    """Every case in a report, and the spellings that name more than one."""

    #: Node id to outcome, holding only spellings that resolve to one case.
    by_spelling: Mapping[str, _CaseOutcome]
    #: Spellings two or more cases share, which therefore resolve to none.
    ambiguous: frozenset[str]


def _case_index(root: Element) -> _CaseIndex:
    """Index every test case in *root* by its node id.

    A runner may write a flat ``testsuite`` or a ``testsuites`` wrapper, so the
    search is over descendants rather than direct children.

    A spelling is dropped rather than overwritten when a second case claims it.
    The bare test name is one of the spellings a manifest may use, and two
    files may hold the same name, so the last case parsed would otherwise
    decide the verdict for a criterion naming the other: a declared assertion
    failure anywhere in the suite could turn a crashing pending test green.

    Returns:
        The resolvable spellings and the ambiguous ones, kept apart so a
        caller can refuse the second rather than silently read one of them.
    """
    index: dict[str, _CaseOutcome] = {}
    ambiguous: set[str] = set()
    for case in root.iter("testcase"):
        # Runners disagree on whether the file is a classname or part of the
        # name, so every spelling is indexed and the manifest may use any.
        outcome = _outcome_of(case)
        for spelling in _spellings(case):
            if spelling in index:
                ambiguous.add(spelling)
                continue
            index[spelling] = outcome
    for spelling in ambiguous:
        del index[spelling]
    return _CaseIndex(by_spelling=index, ambiguous=frozenset(ambiguous))


def _raised_type(outcome: _CaseOutcome) -> str | None:
    """The exception class a runner recorded against *outcome*, if it named one.

    Read from the ``type`` attribute where a runner writes one, and otherwise
    from the message's own leading ``ClassName:`` prefix, which is the shape
    pytest produces: its ``reprcrash.message`` opens with the raised class, so
    a lost assertion reads ``AssertionError: assert 1 == 2`` and a crash reads
    ``KeyError: 'x'``.

    Returns:
        The bare class name, lowercased, or ``None`` when the outcome names no
        class at all (a runner whose messages are free-form prose).
    """
    if outcome.raised:
        return outcome.raised.rsplit(".", maxsplit=1)[-1].strip().lower()
    head, separator, _ = outcome.message.partition(":")
    candidate = head.strip()
    if not separator or not _TYPE_NAME.fullmatch(candidate):
        return None
    return candidate.rsplit(".", maxsplit=1)[-1].lower()


def _asserted(outcome: _CaseOutcome) -> bool:
    """Whether a ``failure`` reads as an assertion the test made.

    The class the runner named wins whenever there is one, because it is the
    structured answer and the message text is not: an unrelated exception
    whose message merely CONTAINS the word ("cannot assert on an empty input")
    reads as a declared failure under a substring test, which turns a crashing
    skeleton green. The markers stay as the fallback for runners that name no
    class, where free-form text is the only signal there is.

    Returns:
        Whether the outcome records a failed assertion.
    """
    raised = _raised_type(outcome)
    if raised is not None:
        return raised.endswith(_ASSERTION_TYPE_SUFFIX)
    lowered = outcome.message.lower()
    return any(marker in lowered for marker in _ASSERTION_MARKERS)


def _verdict_for(outcome: _CaseOutcome | None) -> tuple[PendingVerdict, str]:
    """Decide one pending test's verdict from its recorded outcome.

    Args:
        outcome: What the runner recorded, or ``None`` when the report names no
            such test.

    Returns:
        The verdict and the reason to hand back.
    """
    if outcome is None:
        return (
            PendingVerdict.RED,
            "the report names no such test, so nothing was measured",
        )
    if outcome.tag == _FAILURE_TAG:
        if not _asserted(outcome):
            return (
                PendingVerdict.RED,
                "raised rather than asserting, so the skeleton is wrong not absent",
            )
        return (
            PendingVerdict.GREEN,
            "failed its declared assertion, which is the contract being unimplemented",
        )
    if outcome.tag == _ERROR_TAG:
        return (
            PendingVerdict.RED,
            "raised before it could assert, so the skeleton is wrong not absent",
        )
    if outcome.tag == _SKIPPED_TAG:
        return (
            PendingVerdict.RED,
            "was skipped, so nothing was measured",
        )
    return (
        PendingVerdict.RED,
        "passed while still pending; clear its manifest entry in the same commit",
    )


def classify_pending(
    pending: tuple[PendingTest, ...],
    *,
    workspace_path: Path,
    test_report_path: str | None,
    not_before: datetime | None = None,
) -> PendingReport:
    """Classify every declared pending criterion against the run's report.

    Args:
        pending: The manifest's pending declarations, in order.
        workspace_path: Root the report path is resolved against.
        test_report_path: Manifest-declared report location, or ``None``.
        not_before: When the run being judged executed. A report last written
            before that describes some earlier run, so it is not evidence
            about this one. ``None`` asks for no such correlation.

    Returns:
        One outcome per pending criterion. When the report cannot be read, is
        not parseable, or predates the run, every criterion is red and
        ``report_read`` is ``False``.
    """
    if not pending:
        return PendingReport()
    root = read_report(workspace_path, test_report_path, not_before=not_before)
    if root is None:
        return PendingReport(
            outcomes=tuple(
                CriterionOutcome(
                    criterion=entry.criterion,
                    test_id=entry.test_id,
                    verdict=PendingVerdict.RED,
                    reason="no readable report, so nothing was measured",
                )
                for entry in pending
            ),
            report_read=False,
        )
    index = _case_index(root)
    return PendingReport(
        outcomes=tuple(_classify_one(entry, index=index) for entry in pending),
        other_failures=_count_other_failures(
            root,
            frozenset(str(entry.test_id) for entry in pending),
            index.ambiguous,
        ),
    )


def _classify_one(
    entry: PendingTest,
    *,
    index: _CaseIndex,
) -> CriterionOutcome:
    """Classify a single pending declaration against the indexed report.

    Returns:
        The criterion's outcome.
    """
    if entry.test_id in index.ambiguous:
        return CriterionOutcome(
            criterion=entry.criterion,
            test_id=entry.test_id,
            verdict=PendingVerdict.RED,
            reason=(
                "the report holds more than one test under this name, so which"
                " one the criterion means cannot be established; name it by its"
                " full node id"
            ),
        )
    verdict, reason = _verdict_for(index.by_spelling.get(entry.test_id))
    return CriterionOutcome(
        criterion=entry.criterion,
        test_id=entry.test_id,
        verdict=verdict,
        reason=reason,
    )
