# module-kind: code
"""Did this run deliver what it promised?

One question asked three ways, weakest evidence first: the loop's own
NO_OP classification, the zero-tool-call proxy, and finally the workspace
itself. Kept together because they are one decision, and splitting them
across a caller made the order they must be asked in a matter of reading
control flow rather than of reading one function.

This module only decides, and returns the operator-facing reason when the
answer is no. Moving the task is the caller's job (``task_sync``), which is
what keeps the two separable: a silent no-op success is a failure, and the
evidence for it is a different concern from the status write it drives.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.artifacts.baseline_scope import current_artifact_baseline
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    ExpectedArtifactProbe,
)
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
    artifact_probe: ExpectedArtifactProbe | None,
) -> str | None:
    """Return why this run delivered nothing, or ``None`` when it did.

    Args:
        run: The finished run.
        ctx: Its context, carrying the task and its project.
        artifact_probe: The wired workspace probe, or ``None``.

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
    return await _workspace_verdict(
        ctx, artifact_probe, empty_run_fails=empty_run_fails
    )


async def _workspace_verdict(
    ctx: AgentContext,
    artifact_probe: ExpectedArtifactProbe | None,
    *,
    empty_run_fails: bool,
) -> str | None:
    """Ask the workspace the question the tool-call count only proxies.

    An agent that read files, wrote nothing and stopped passes the proxy, so
    the declared deliverables are checked on disk. Deliberately not exempted
    for a resumed run on the presence arm: the resume exemption exists
    because this segment's turn count says nothing about earlier segments,
    and the filesystem has no such blind spot.

    Returns:
        The failure reason, or ``None`` when the run may proceed to review.
    """
    presence = await _absent_artifacts(artifact_probe, ctx)
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
        current_artifact_baseline()
    ):
        return UNCHANGED_ARTIFACTS_REASON.format(paths=", ".join(presence.probed))
    return None


async def _absent_artifacts(
    artifact_probe: ExpectedArtifactProbe | None,
    ctx: AgentContext,
) -> ArtifactPresence | None:
    """Ask the workspace which declared artifacts are missing.

    Args:
        artifact_probe: The wired probe, or ``None`` when the engine was
            built without a workspace root to resolve against.
        ctx: The finished run's context, carrying the task and its project.

    Returns:
        What the workspace says, or ``None`` when the question could not be
        asked -- no probe, no project, or a probe that raised.

        ``None`` lets review proceed rather than failing the task, because a
        storage fault is not evidence an agent delivered nothing. It is not
        silent: a probe that raised is logged at ERROR, and the same fault
        makes the deliverable reader hand the reviewer an explicit
        unreadable-workspace marker, so the run reaches review carrying the
        fact that it could not be verified rather than looking verified.
    """
    if artifact_probe is None or ctx.task_execution is None:
        return None
    task = ctx.task_execution.task
    project_id = str(task.project)
    if not project_id.strip():
        return None
    try:
        return await artifact_probe(project_id, task.artifacts_expected)
    except OSError as exc:
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
