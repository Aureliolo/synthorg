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
all"; the message is what answers "did it assert", and both are read.
"""

import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import parse as parse_xml
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.manifest import PendingTest
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_PENDING_REPORT_ESCAPED,
    ENVIRONMENT_PENDING_REPORT_UNREADABLE,
)

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
#: the one thing the pending marker is not allowed to cover. The message is
#: where the distinction survives, so it is what gets read.
#:
#: Matched case-insensitively anywhere in the message, and deliberately a small
#: declared vocabulary rather than a denylist of exception types, which is
#: unbounded. An unrecognised message reads RED: a pending test the runner
#: cannot be shown to have asserted is exactly the case the operator should see.
_ASSERTION_MARKERS: Final[tuple[str, ...]] = (
    "assert",
    "expect(",
)

#: The JUnit child element a runner writes when the test did not run. A pending
#: test that was skipped measured nothing, so it cannot be evidence that the
#: contract is merely unimplemented.
_SKIPPED_TAG: Final[str] = "skipped"

#: Ceiling on a report the parser will build a tree from. A report is one run's
#: per-test results, so a real one is orders of magnitude under this; the limit
#: exists because the file is written inside a workspace an agent controls and
#: the parse happens on the API's own event loop, where an unbounded tree is a
#: memory ceiling somebody else picks.
_MAX_REPORT_BYTES: Final[int] = 32 * 1024 * 1024


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

    The message travels with the tag because the tag alone does not separate an
    assertion from an unrelated exception; see :data:`_ASSERTION_MARKERS`.
    """

    #: The outcome element's tag, or ``None`` for a pass.
    tag: str | None
    #: The outcome's ``message`` attribute, empty when it carries none.
    message: str


def _outcome_of(case: Element) -> _CaseOutcome:
    """Return the JUnit outcome recorded inside *case*.

    Returns:
        The first recognised outcome child's tag and message, or a tag of
        ``None`` when the case carries none, which is how a runner records a
        pass.
    """
    for tag in (_ERROR_TAG, _FAILURE_TAG, _SKIPPED_TAG):
        element = case.find(tag)
        if element is not None:
            return _CaseOutcome(tag=tag, message=element.get("message", ""))
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


def _count_other_failures(root: Element, pending_ids: frozenset[str]) -> int:
    """Count failing cases the manifest did not declare pending.

    Counted from the cases rather than from the index, because the index holds
    each case under three spellings and would treble every failure.

    Returns:
        How many non-pending cases failed or errored. A skip is not one: a
        skipped ordinary test is a decision the suite made, not a break.
    """
    return sum(
        1
        for case in root.iter("testcase")
        if (spellings := _spellings(case))
        and not pending_ids.intersection(spellings)
        and _outcome_of(case).tag in {_FAILURE_TAG, _ERROR_TAG}
    )


def _case_index(root: Element) -> Mapping[str, _CaseOutcome]:
    """Index every test case in *root* by its node id.

    A runner may write a flat ``testsuite`` or a ``testsuites`` wrapper, so the
    search is over descendants rather than direct children.

    Returns:
        A mapping of node id to its outcome, whose tag is ``None`` for a pass.
    """
    index: dict[str, _CaseOutcome] = {}
    for case in root.iter("testcase"):
        # Runners disagree on whether the file is a classname or part of the
        # name, so every spelling is indexed and the manifest may use any.
        outcome = _outcome_of(case)
        for spelling in _spellings(case):
            index[spelling] = outcome
    return index


def _asserted(message: str) -> bool:
    """Whether a ``failure``'s message reads as an assertion the test made.

    Returns:
        Whether any declared marker appears in *message*.
    """
    lowered = message.lower()
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
        if not _asserted(outcome.message):
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
    root = _read_report(workspace_path, test_report_path, not_before=not_before)
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
            root, frozenset(str(entry.test_id) for entry in pending)
        ),
    )


def _classify_one(
    entry: PendingTest,
    *,
    index: Mapping[str, _CaseOutcome],
) -> CriterionOutcome:
    """Classify a single pending declaration against the indexed report.

    Returns:
        The criterion's outcome.
    """
    verdict, reason = _verdict_for(index.get(entry.test_id))
    return CriterionOutcome(
        criterion=entry.criterion,
        test_id=entry.test_id,
        verdict=verdict,
        reason=reason,
    )


def _read_report(
    workspace_path: Path,
    test_report_path: str | None,
    *,
    not_before: datetime | None,
) -> Element | None:
    """Read and parse the run's report.

    A path escaping the workspace is refused rather than followed, and the parse
    is entity-hardened: the manifest is committed content an agent can write, so
    both the path and the bytes it names are untrusted input even though the
    manifest is the authority on what is pending. An expanded external entity
    here would read the backend's filesystem on the agent's behalf.

    Three things about the file are checked before its contents are believed.
    It must be a regular file: a named pipe passes every path check and then
    blocks the parse for ever, which on this call path is the whole event loop.
    It must be small enough to hold: the parser builds a tree, so a report the
    size of a disk is a memory ceiling an agent chooses. And it must be at
    least as new as the run it is offered as evidence about, because one report
    path is shared by every unit in the project and nothing rewrites it when a
    run dies before producing one.

    Returns:
        The parsed root element, or ``None`` when the report is absent, outside
        the workspace, not a regular file, too large, older than the run, or
        unparseable.
    """
    if test_report_path is None:
        # Declared pending criteria with no report to classify them from is
        # refused at the model, so reaching this means a project declared
        # pending entries some other way. Logged rather than returned in
        # silence, because it lands every criterion red and the operator would
        # otherwise see the verdict with nothing naming its cause.
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=None,
            reason="no_report_declared",
        )
        return None
    root = workspace_path.resolve()
    resolved = (root / test_report_path).resolve()
    if not resolved.is_relative_to(root):
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_ESCAPED,
            report_path=test_report_path,
        )
        return None
    try:
        if not _is_usable_evidence(resolved, test_report_path, not_before):
            return None
        return parse_xml(resolved).getroot()
    except (OSError, ValueError, ParseError, DefusedXmlException) as exc:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=test_report_path,
            error_type=type(exc).__name__,
        )
        return None


def _is_usable_evidence(
    resolved: Path,
    declared: str,
    not_before: datetime | None,
) -> bool:
    """Whether the file at *resolved* can stand as evidence about this run.

    Returns:
        Whether it is a regular file, within the size ceiling, and no older
        than the run being judged.

    Raises:
        OSError: Propagated from the stat, and handled by the caller alongside
            every other way the file can refuse to be read.
    """
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=declared,
            reason="not_a_regular_file",
        )
        return False
    if info.st_size > _MAX_REPORT_BYTES:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=declared,
            reason="report_too_large",
            size_bytes=info.st_size,
        )
        return False
    if not_before is None:
        return True
    written = datetime.fromtimestamp(info.st_mtime, tz=UTC)
    if written < not_before:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=declared,
            reason="report_predates_the_run",
        )
        return False
    return True
