# module-kind: code
"""What a project's pending declaration says about one task's test run.

A skeleton commits one test per acceptance criterion before anything implements
the contract, so a CORRECT skeleton's suite exits non-zero. Read as evidence,
that run blocks the very deliverable the contract stage exists to produce.

The correction belongs here rather than on the record. ``CodeExecutionRecord``
answers "did this command exit zero", enforced by a validator and a database
CHECK, and that is a measurement worth keeping honest: a caller that could write
``passed`` freely could mint a green build from any run at all. The oracle is
what turns measurements into a verdict, so the declaration is read here, where
the verdict already lives, and the row keeps saying what actually happened.

Two questions, because the exit status can answer neither and they point in
opposite directions.

**Was this failure declared?** A run whose pending tests all failed their own
declared assertions, with nothing else broken, is forgiven. Any other pending
outcome, or an ordinary test broken alongside them, is not. One bit of exit
status cannot say which, because the declared failures have already spent it.

**Is this unit's own criterion still outstanding?** Clearing its manifest entry
in the same commit is the mechanical signal that a unit is done, so a unit whose
suite went green while its own criterion is still listed did not give that
signal, and the next unit inherits a criterion the manifest calls unimplemented.
Judged per criterion rather than per project: a project mid-build always has
other units' entries outstanding, and reading those would fail every unit until
the last one.

**Forgiveness is bound to the criteria the plan was approved with.** The
manifest is a committed file inside a workspace the agent can write, and the two
questions above key on different fields: forgiveness on the test id, strictness
on the criterion text, which is free-form. So an agent whose own build is red
could append one entry naming an invented criterion and its own failing test,
and buy a VERIFIED verdict: the failure is "declared", and the invented
criterion intersects no task's criteria, so nothing is outstanding either. It
scales to an arbitrarily red build. The plan's own objective criteria are the
vocabulary the operator approved and the same one the skeleton's brief tells it
to write an entry for, so an entry outside that vocabulary forgives nothing.
Its test then counts as an ordinary break, which turns the forge into a refusal
rather than merely declining to help.

**The report has to be newer than the run it speaks for.** The workspace is per
project, not per task, so one report file is shared by every unit and every
attempt, and nothing rewrites it when a run dies before writing one. A unit
whose suite timed out would otherwise have the skeleton's own leftover report
read against it, every pending test failing its own assertion, and its failing
run forgiven. So a report older than the run being judged is not evidence about
that run.

**A manifest that exists and will not parse blocks.** It is a committed file an
agent authored, so a broken one is a defect its author has to fix. Reading it as
"nothing was pending" is worse than blocking: it hands the verdict back to an
exit status whose declaration nobody can check, and it silently waives the
declared gates and the clear-your-own-marker rule at the same time, so a task
completes VERIFIED with a reason indistinguishable from a compliant one. A
workspace with NO manifest is a different fact entirely: nothing was ever
declared, and the run's status stands.

Every read is one pass. The manifest and the report are parsed once per verdict
and handed around as a :class:`ContractView`, because these questions are asked
together and three independent loads meant three stats, three parses and three
copies of the same warning on a surface the dashboard polls every thirty
seconds. The parse itself runs off the event loop: it is attacker-authored YAML
in a directory an agent writes, reached from the API request path.
"""

import asyncio
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.criterion_match import criterion_key
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.engine.workspace.environment.manifest import (
    EnvironmentManifest,
    read_manifest,
)
from synthorg.engine.workspace.environment.pending import classify_pending
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_PENDING_MANIFEST_UNREAD
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose

logger = get_logger(__name__)

#: Sentence-ending punctuation dropped before comparing two criteria. Only the
#: trailing run: punctuation inside a criterion is part of what it says.
_TRAILING_PUNCTUATION: Final[str] = ".!?;:, "


class ContractState(StrEnum):
    """Whether the project's committed contract could be read at all.

    Three states because they need three different answers, and collapsing any
    two of them is a way for the gate to stop guarding. ``ABSENT`` declares
    nothing, so the run's own status stands. ``UNREADABLE`` declares something
    nobody can check, so the task blocks on that. ``READ`` is the ordinary case.
    """

    ABSENT = "absent"
    UNREADABLE = "unreadable"
    READ = "read"


class ContractView(BaseModel):
    """One project's committed contract, read once for a whole verdict."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    state: ContractState
    #: The directory the manifest was read from, which the report path is
    #: resolved against. Carried rather than re-derived, since deriving it
    #: twice is two answers to where the project lives.
    workspace: Path | None = None
    manifest: EnvironmentManifest | None = None


def _read(workspace_root: Path | None, project_id: str) -> ContractView:
    """Read the project's manifest from disk.

    Returns:
        What was found, and the manifest when there was one to parse.
    """
    if workspace_root is None:
        return ContractView(state=ContractState.ABSENT)
    workspace = project_workspace_dir(workspace_root, project_id)
    if not (workspace / DEFAULT_MANIFEST_FILENAME).is_file():
        return ContractView(state=ContractState.ABSENT)
    try:
        manifest = read_manifest(workspace, filename=DEFAULT_MANIFEST_FILENAME)
    except EnvironmentConfigError as exc:
        logger.warning(
            ENVIRONMENT_PENDING_MANIFEST_UNREAD,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ContractView(state=ContractState.UNREADABLE)
    return ContractView(
        state=ContractState.READ, workspace=workspace, manifest=manifest
    )


async def load_contract(
    *,
    workspace_root: Path | None,
    project_id: str,
) -> ContractView:
    """Read the project's committed contract, once, off the event loop.

    Args:
        workspace_root: Base directory projects live under, or ``None`` when
            the oracle was wired without one, which reads as nothing declared
            rather than guessing at a workspace.
        project_id: The project whose workspace holds the manifest.

    Returns:
        The contract view every question below is answered from.
    """
    return await asyncio.to_thread(_read, workspace_root, project_id)


def _match_key(criterion: str) -> str:
    """Reduce a criterion to what the two sides of this match can agree on.

    ``criterion_key`` is the shared normaliser and is applied first, but it
    keeps trailing punctuation, and the two texts here have different authors:
    one is the objective's own wording, the other is what an agent typed into
    the manifest after reading it in a brief. A full stop it dropped would make
    every comparison miss, and a check that never matches passes every unit
    silently, which is the worst way for a gate to fail.

    Returns:
        The comparison key.
    """
    return criterion_key(criterion).rstrip(_TRAILING_PUNCTUATION)


def approved_vocabulary(criteria: Iterable[str]) -> frozenset[str]:
    """The comparison keys an approved objective's criteria reduce to.

    Returns:
        One key per criterion, which is the set a manifest entry has to name
        for its declaration to count.
    """
    return frozenset(_match_key(criterion) for criterion in criteria)


def failure_was_declared(
    contract: ContractView,
    *,
    approved: frozenset[str],
    not_before: datetime | None,
) -> bool:
    """Whether a failing test run is exactly the failure the project declared.

    Args:
        contract: The project's contract, already read.
        approved: The criterion keys the plan was approved with. An entry
            naming anything else declares nothing, and its test is then read
            as an ordinary break rather than a forgiven one.
        not_before: When the run being judged executed. A report written
            before that is about some earlier run and is not evidence here.

    Returns:
        ``True`` only when the manifest declares pending tests against approved
        criteria and the run's own report shows every one of them failing its
        own assertion with nothing else broken.
    """
    if contract.manifest is None or contract.workspace is None:
        return False
    declared = tuple(
        entry
        for entry in contract.manifest.pending
        if _match_key(str(entry.criterion)) in approved
    )
    if not declared:
        return False
    return classify_pending(
        declared,
        workspace_path=contract.workspace,
        test_report_path=contract.manifest.test_report_path,
        not_before=not_before,
    ).green


def unclaimed_criteria(
    contract: ContractView,
    criteria: Iterable[str],
) -> tuple[str, ...]:
    """Which of a task's own criteria the manifest still calls unimplemented.

    Args:
        contract: The project's contract, already read.
        criteria: The task's acceptance criteria, in its own spelling.

    Returns:
        The criteria still listed pending, in the task's order. Empty when the
        unit cleared its entries or the project declares none.
    """
    if contract.manifest is None:
        return ()
    outstanding = {
        _match_key(str(entry.criterion)) for entry in contract.manifest.pending
    }
    return tuple(
        criterion for criterion in criteria if _match_key(criterion) in outstanding
    )


def declared_gates(contract: ContractView) -> Mapping[CodeExecutionPurpose, str]:
    """The gate commands the project declares, by what each one proves.

    The oracle asks for a passing run of each, so a gate the manifest declares
    and the run never exercised is a unit that is not finished. Derived from
    the manifest rather than listed anywhere, which is what stops a field being
    added that nothing ever requires evidence of.

    Returns:
        One entry per declared gate; empty when the project declares none.
    """
    if contract.manifest is None:
        return {}
    return contract.manifest.declared_gates


__all__ = [
    "ContractState",
    "ContractView",
    "approved_vocabulary",
    "declared_gates",
    "failure_was_declared",
    "load_contract",
    "unclaimed_criteria",
]
