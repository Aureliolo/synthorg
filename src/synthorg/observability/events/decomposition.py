"""Decomposition event constants."""

from typing import Final

DECOMPOSITION_STARTED: Final[str] = "decomposition.started"
DECOMPOSITION_COMPLETED: Final[str] = "decomposition.completed"
DECOMPOSITION_SUBTASK_CREATED: Final[str] = "decomposition.subtask.created"
DECOMPOSITION_VALIDATION_ERROR: Final[str] = "decomposition.validation.error"
DECOMPOSITION_STRUCTURE_CLASSIFIED: Final[str] = "decomposition.structure.classified"
DECOMPOSITION_ROLLUP_COMPUTED: Final[str] = "decomposition.rollup.computed"
DECOMPOSITION_GRAPH_VALIDATED: Final[str] = "decomposition.graph.validated"
DECOMPOSITION_GRAPH_CYCLE: Final[str] = "decomposition.graph.cycle"
DECOMPOSITION_FAILED: Final[str] = "decomposition.failed"
DECOMPOSITION_CEILING_UNREADABLE: Final[str] = "decomposition.ceiling.unreadable"
"""A wall-clock ceiling could not be read, so the definition's default stands.

Its own event rather than a note on ``decomposition.failed``, because nothing
failed: the decomposition proceeds, under a bound the operator did not choose.
That is the shape worth being able to grep for on its own, since the ceiling is
re-read per decomposition and a deployment that raised one can be running under
the default for as long as the read keeps failing."""
DECOMPOSITION_REFERENCE_ERROR: Final[str] = "decomposition.reference.error"
DECOMPOSITION_GRAPH_BUILT: Final[str] = "decomposition.graph.built"
DECOMPOSITION_LLM_CALL_START: Final[str] = "decomposition.llm.call.start"
DECOMPOSITION_LLM_CALL_COMPLETE: Final[str] = "decomposition.llm.call.complete"
DECOMPOSITION_LLM_PARSE_ERROR: Final[str] = "decomposition.llm.parse.error"
DECOMPOSITION_LLM_RETRY: Final[str] = "decomposition.llm.retry"

# Agent-session decomposition strategy: the owner-run planning loop.
DECOMPOSITION_SESSION_STARTED: Final[str] = "decomposition.session.started"
DECOMPOSITION_SESSION_COMPLETED: Final[str] = "decomposition.session.completed"
DECOMPOSITION_SESSION_NO_PLAN: Final[str] = "decomposition.session.no_plan"
DECOMPOSITION_SESSION_FALLBACK: Final[str] = "decomposition.session.fallback"
DECOMPOSITION_SESSION_TOOL_DROPPED: Final[str] = "decomposition.session.tool_dropped"
DECOMPOSITION_SESSION_DUPLICATE_SUBMIT: Final[str] = (
    "decomposition.session.duplicate_submit"
)

DECOMPOSITION_SESSION_RESUMED: Final[str] = "decomposition.session.resumed"
"""A planning session that stopped without submitting was told so and continued.

Carries the resume count and the turns already spent against the cap, because
the question this answers on a slow or expensive run is whether the session is
converging on a plan or burning its budget being handed the same rejection."""

DECOMPOSITION_SESSION_PLAN_REJECTED: Final[str] = "decomposition.session.plan_rejected"
"""A submitted plan was refused, so the agent was handed the reason to fix.

The refusal itself reaches the agent as the tool's own result and never has to
be logged for it to act. This exists for the reader afterwards: the same
rejection arriving turn after turn is what a session burning its budget without
converging looks like, and nothing else records the reason each time."""

DECOMPOSITION_SESSION_PLAN_RESUBMITTED: Final[str] = (
    "decomposition.session.plan_resubmitted"
)
"""A refused plan came back byte-identical, so the refusal was reframed.

An unchanged resubmission carries no information: it cannot be accepted, and
answering it with the wording that already failed to land buys the same turn
again. Two of five repair rounds on one parent in a live run were exactly this.
Carries how many times this plan has now been submitted, because that count is
what separates a model correcting itself from one that is stuck."""

DECOMPOSITION_SESSION_ARGUMENTS_MANGLED: Final[str] = (
    "decomposition.session.arguments_mangled"
)
"""A tool call arrived with its repeated fields collapsed into nesting.

The transport's fault rather than the model's, so it is reported separately
from a rejected plan: the plan was never read. Frequency is the point, since
the fix belongs upstream of here if one model keeps producing it, so the line
carries ``mangled_calls``: the running count within this session, which is the
number the question is actually asked of."""

DECOMPOSITION_SESSION_DIGEST_FALLBACK: Final[str] = (
    "decomposition.session.digest_fallback"
)
"""A submission's arguments would not serialise, so the digest used their repr.

Should not happen: these arguments are already decoded JSON by the time they
reach the tool. A provider producing a shape that lands here repeatedly is a
finding about that provider, and nothing else records it."""

DECOMPOSITION_LLM_ARGUMENTS_MANGLED: Final[str] = "decomposition.llm.arguments_mangled"
"""The single-shot strategy's reply arrived with its list flattened.

The sibling of ``DECOMPOSITION_SESSION_ARGUMENTS_MANGLED`` on the other
planning path. Carries the round count rather than the attempt, because this
round deliberately does NOT spend one of the operator's planning attempts: the
reply never carried a plan to judge."""

# Coordination-constraints middleware received an empty plan text from
# the LLM.  Caller falls back to a default plan; the event preserves the
# failure mode for triage.
DECOMPOSITION_EMPTY_PLAN_TEXT: Final[str] = "decomposition.empty_plan_text"

DECOMPOSITION_SUBTASK_OVERSIZED: Final[str] = "decomposition.subtask.oversized"
"""A subtask declared more work than one agent's worth, so it was split again.

Carries the condition that fired and the counts behind it, because the
alternative account of a deeper tree is a planner that simply produced more
items, and the two want different fixes."""

DECOMPOSITION_DEPTH_EXHAUSTED: Final[str] = "decomposition.depth_exhausted"
"""An oversized subtask was dispatched whole because the depth budget ran out.

The one outcome recursion exists to prevent, recorded where it happens: the
task still runs, so nothing else downstream reports that it was known to be
too big for the agent that got it."""

DECOMPOSITION_RECURSED: Final[str] = "decomposition.recursed"
"""One level of a recursive decomposition finished."""

DECOMPOSITION_MODEL_UNSET: Final[str] = "decomposition.model_unset"
"""No explicit provider + model pair is bound for decomposition.

The runtime then builds without a coordinator. Its own event so that
outcome is attributable on its own terms rather than only through the
degraded-mode line that follows it."""

DECOMPOSITION_REBUILT_FROM_PLAN: Final[str] = "decomposition.rebuilt_from_plan"
"""An approved plan was projected back onto a dispatchable decomposition.

Emitted on the approval-dispatch path, where the reviewed plan (not the
snapshot captured at gate time) decides what builds. It records how many
items were dropped as decisions and how many edges survived, which is the
only place the difference between what an operator approved and what was
dispatched is visible."""
