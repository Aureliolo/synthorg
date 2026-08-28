# module-kind: code
"""Did this run deliver what it promised?

One question asked in order, weakest evidence first: the loop's own NO_OP
classification, the zero-tool-call proxy, and finally the workspace itself.
Kept together because they are one decision, and splitting them across a
caller made the order they must be asked in a matter of reading control flow
rather than of reading one function.

The workspace is asked two things, and the wider one is asked first. "Did
this run change the tree at all" needs no plan and cannot be wrong about a
file named differently from the guess a planner made before the tree existed;
"are the declared artifacts there" is the sharper question and the one whose
answer an operator can act on, so it supplies the reason once the first says
something happened but the declarations disagree. A run that produced
something and satisfied no declaration reaches review, where a reviewer can
read what it produced instead; a run that changed nothing is failed here.

This module only decides, and returns the operator-facing reason when the
answer is no. Moving the task is the caller's job (``task_sync``), which is
what keeps the two separable: a silent no-op success is a failure, and the
evidence for it is a different concern from the status write it drives.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.artifacts.baseline_scope import (
    RunBaselineProbe,
    current_run_baseline,
    produced_nothing_since,
)
from synthorg.engine.artifacts.expected_artifact_check import ArtifactPresence
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)

# Reason surfaced when a work task finishes with no produced artifacts and
# no recorded no-op justification: the run is failed rather than pushed to
# review as a silent no-op success.
EMPTY_RUN_REASON: Final[str] = (
    "Run produced no artifacts and no tool calls; failing the task instead "
    "of recording a silent no-op success"
)

# Reason surfaced when a work task declared artifacts and produced none of
# them. ``{paths}`` names the declared paths, so the operator reads what was
# promised rather than that something unnamed went wrong.
MISSING_ARTIFACTS_REASON: Final[str] = (
    "Run produced none of its declared artifacts ({paths}); failing the task "
    "instead of sending an empty deliverable to review"
)

# Reason surfaced when every declared artifact is byte-identical to how the
# run found it. Named separately from the missing case because the operator
# is looking at files that are present and correct-looking, and needs telling
# that the run did not touch them.
UNCHANGED_ARTIFACTS_REASON: Final[str] = (
    "Run left every declared artifact ({paths}) exactly as it found it; "
    "failing the task instead of sending unchanged work to review"
)

# Reason surfaced when the whole workspace is byte-for-byte as the run found
# it. Names no path, deliberately: nothing was written anywhere, so a list of
# declarations would suggest the run went wrong at those paths rather than
# never having produced anything at all.
NOTHING_PRODUCED_REASON: Final[str] = (
    "Run left its workspace exactly as it found it, producing no file "
    "anywhere; failing the task instead of sending an empty deliverable to "
    "review"
)

# Extension point for a legitimately empty run (e.g. a task that concluded no
# change was needed): its presence routes an otherwise-empty run to review
# instead of FAILED. The invariant is fail-closed today -- no production path
# sets this key, so an empty work run always fails. When a producer is wired,
# it MUST be a system/pipeline-set, validated signal, never a value derived
# from agent/LLM output, so an agent cannot self-justify an empty run.
NO_OP_JUSTIFICATION_KEY: Final[str] = "no_op_justification"


async def no_delivery_reason(
    run: ExecutionResult,
    ctx: AgentContext,
    *,
    run_probe: RunBaselineProbe | None,
) -> str | None:
    """Return why this run delivered nothing, or ``None`` when it did.

    Args:
        run: The finished run.
        ctx: Its context, carrying the task and its project.
        run_probe: The wired workspace probe, or ``None``.

    Returns:
        The operator-facing failure reason, or ``None`` when the run may
        proceed to review.
    """
    if run.metadata.get(NO_OP_JUSTIFICATION_KEY):
        # Recording why nothing was produced is the one sanctioned way to
        # finish a run empty-handed, and it answers every question below.
        return None
    task_execution = ctx.task_execution
    expects_artifacts = task_execution is not None and bool(
        task_execution.task.artifacts_expected
    )
    # A resumed/replayed run only carries the current segment's turns, so its
    # zero-tool-call count is not a valid proxy for total task output: earlier
    # segments (before an approval park) may already have produced artifacts.
    # Exempt a continued run from the empty-run failure so a legitimately
    # progressed task is never discarded; a genuinely empty continued run
    # still completes to review rather than FAILED.
    empty_run_fails = not is_resumed_run()

    # A silent no-op success is a failure: a WORK task (one that declared
    # expected artifacts) that produced none (proxied by zero tool calls) is
    # failed unless an explicit no-op justification was recorded. Enforced in
    # two layers: the react loop classifies the empty run as NO_OP, and this
    # also guards a COMPLETED that slipped through from another loop.
    if empty_run_fails and (
        run.termination_reason == TerminationReason.NO_OP
        or (expects_artifacts and run.total_tool_calls == 0)
    ):
        return EMPTY_RUN_REASON
    if not expects_artifacts:
        return None
    return await _workspace_verdict(ctx, run_probe, empty_run_fails=empty_run_fails)


async def _workspace_verdict(
    ctx: AgentContext,
    run_probe: RunBaselineProbe | None,
    *,
    empty_run_fails: bool,
) -> str | None:
    """Ask the workspace the question the tool-call count only proxies.

    An agent that read files, wrote nothing and stopped passes the proxy, so
    the workspace is asked directly. Deliberately not exempted for a resumed
    run on the presence arm: the resume exemption exists because this
    segment's turn count says nothing about earlier segments, and the
    filesystem has no such blind spot.

    Returns:
        The failure reason, or ``None`` when the run may proceed to review.
    """
    baseline = current_run_baseline()
    produced_nothing = await produced_nothing_since(baseline)
    if produced_nothing is False:
        # Something appeared, changed or went. The declarations may still all
        # be absent, because a planner names paths before the tree exists and
        # an agent that solved the task under other names satisfies none of
        # them; that is a judgement about substituted work, which is the
        # reviewer's, not an empty run, which is this guard's.
        return None
    presence = await _absent_artifacts(run_probe, ctx)
    if presence is None:
        return None
    if presence.nothing_delivered:
        return MISSING_ARTIFACTS_REASON.format(paths=", ".join(presence.missing))
    # Presence answers a task that creates. A task that edits found its
    # declarations already there, so only the baseline separates a run that
    # fixed the file from one that read it and stopped. Exempted for a resumed
    # run, whose baseline was taken at the resume and so already contains
    # whatever an earlier segment wrote: this segment changing nothing is not
    # the same as the task having produced nothing.
    if empty_run_fails and presence.delivered_nothing_since(
        baseline.declared if baseline is not None else None
    ):
        return UNCHANGED_ARTIFACTS_REASON.format(paths=", ".join(presence.probed))
    # Last, because the two reasons above name a path an operator can open
    # and this one names nothing. It is the only answer for a task whose
    # declarations are all prose ("the integrated, runnable deliverable"),
    # which nothing above can probe and which therefore had no delivery check
    # at all: the reviewer read a description of work that was never done.
    if produced_nothing and empty_run_fails:
        return NOTHING_PRODUCED_REASON
    return None


async def _absent_artifacts(
    run_probe: RunBaselineProbe | None,
    ctx: AgentContext,
) -> ArtifactPresence | None:
    """Ask the workspace which declared artifacts are there now.

    Args:
        run_probe: The wired probe, or ``None`` when the engine was
            built without a workspace root to resolve against.
        ctx: The finished run's context, carrying the task and its project.

    Returns:
        What the workspace says, or ``None`` when the question could not be
        asked -- no probe, no project, or a probe that raised.

        ``None`` lets review proceed rather than failing the task, because a
        storage fault is not evidence an agent delivered nothing. Every route
        to it says so: the caller only asks about a task that DECLARED
        deliverables, so each of these is a declared-artifact run reaching
        review unverified, and one that reported nothing would be
        indistinguishable from a run the workspace confirmed. The probe fault
        additionally makes the deliverable reader hand the reviewer an
        explicit unreadable-workspace marker.
    """
    if run_probe is None:
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            reason="no workspace probe is wired; declared artifacts unverified",
        )
        return None
    if ctx.task_execution is None:
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            reason="run carries no task execution; nothing to probe against",
        )
        return None
    task = ctx.task_execution.task
    project_id = str(task.project)
    if not project_id.strip():
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            task_id=str(task.id),
            reason="task names no project; its workspace cannot be resolved",
        )
        return None
    try:
        return (await run_probe(project_id, task.artifacts_expected)).declared
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
        # lint-allow: swallow-ok -- a degraded probe must let the review run
        # on an unverified answer rather than fail the delivered work. The
        # probe is an injected callable reaching a workspace, a store or a
        # container, so narrowing this to OSError decided which of its
        # failure types were survivable by which layer happened to raise.
        reraise_critical(exc)
        logger.error(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            task_id=str(task.id),
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = [
    "EMPTY_RUN_REASON",
    "MISSING_ARTIFACTS_REASON",
    "NO_OP_JUSTIFICATION_KEY",
    "UNCHANGED_ARTIFACTS_REASON",
    "no_delivery_reason",
]
