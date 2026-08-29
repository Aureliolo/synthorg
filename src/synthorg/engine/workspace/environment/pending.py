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

The JUnit shape is what every runner this product meets already emits, and its
two failure elements are exactly the distinction being drawn: ``failure`` is an
assertion the test made and lost, ``error`` is the test never getting that far.
"""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import parse as parse_xml
from pydantic import BaseModel, ConfigDict

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

#: The JUnit child element a runner writes when the test raised before it could
#: assert anything, which covers both a collection error and an unexpected
#: exception. Kept apart from a failure precisely because the two mean opposite
#: things about whether the skeleton loads.
_ERROR_TAG: Final[str] = "error"

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
    other_failures: int = 0

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


def _outcome_tag(case: Element) -> str | None:
    """Return the JUnit outcome element inside *case*, if any.

    Returns:
        The tag name of the first recognised outcome child, or ``None`` when the
        case carries none, which is how a runner records a pass.
    """
    for tag in (_ERROR_TAG, _FAILURE_TAG, _SKIPPED_TAG):
        if case.find(tag) is not None:
            return tag
    return None


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
    if not classname:
        return (name,)
    return (name, f"{classname}::{name}", f"{classname}.{name}")


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
        and _outcome_tag(case) in {_FAILURE_TAG, _ERROR_TAG}
    )


def _case_index(root: Element) -> Mapping[str, str | None]:
    """Index every test case in *root* by its node id.

    A runner may write a flat ``testsuite`` or a ``testsuites`` wrapper, so the
    search is over descendants rather than direct children.

    Returns:
        A mapping of node id to its outcome tag, where ``None`` is a pass.
    """
    index: dict[str, str | None] = {}
    for case in root.iter("testcase"):
        # Runners disagree on whether the file is a classname or part of the
        # name, so every spelling is indexed and the manifest may use any.
        outcome = _outcome_tag(case)
        for spelling in _spellings(case):
            index[spelling] = outcome
    return index


def _verdict_for(outcome: str | None, *, present: bool) -> tuple[PendingVerdict, str]:
    """Decide one pending test's verdict from its recorded outcome.

    Returns:
        The verdict and the reason to hand back.
    """
    if not present:
        return (
            PendingVerdict.RED,
            "the report names no such test, so nothing was measured",
        )
    if outcome == _FAILURE_TAG:
        return (
            PendingVerdict.GREEN,
            "failed its declared assertion, which is the contract being unimplemented",
        )
    if outcome == _ERROR_TAG:
        return (
            PendingVerdict.RED,
            "raised before it could assert, so the skeleton is wrong not absent",
        )
    if outcome == _SKIPPED_TAG:
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
) -> PendingReport:
    """Classify every declared pending criterion against the run's report.

    Args:
        pending: The manifest's pending declarations, in order.
        workspace_path: Root the report path is resolved against.
        test_report_path: Manifest-declared report location, or ``None``.

    Returns:
        One outcome per pending criterion. When the report cannot be read or
        parsed, every criterion is red and ``report_read`` is ``False``.
    """
    if not pending:
        return PendingReport()
    root = _read_report(workspace_path, test_report_path)
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
    index: Mapping[str, str | None],
) -> CriterionOutcome:
    """Classify a single pending declaration against the indexed report.

    Returns:
        The criterion's outcome.
    """
    present = entry.test_id in index
    verdict, reason = _verdict_for(index.get(entry.test_id), present=present)
    return CriterionOutcome(
        criterion=entry.criterion,
        test_id=entry.test_id,
        verdict=verdict,
        reason=reason,
    )


def _read_report(
    workspace_path: Path,
    test_report_path: str | None,
) -> Element | None:
    """Read and parse the run's report.

    A path escaping the workspace is refused rather than followed, and the parse
    is entity-hardened: the manifest is committed content an agent can write, so
    both the path and the bytes it names are untrusted input even though the
    manifest is the authority on what is pending. An expanded external entity
    here would read the backend's filesystem on the agent's behalf.

    Returns:
        The parsed root element, or ``None`` when the report is absent, outside
        the workspace, or unparseable.
    """
    if test_report_path is None:
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
        return parse_xml(resolved).getroot()
    except (OSError, ParseError, DefusedXmlException) as exc:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=test_report_path,
            error_type=type(exc).__name__,
        )
        return None
