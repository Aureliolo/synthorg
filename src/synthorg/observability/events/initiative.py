"""Initiative-tail event constants.

The stages between "every plan item is done" and delivery: assembling the
verified pieces into one running deliverable, scoring that whole against the
objective's success criteria, and replanning an initiative that can no longer
advance. Distinct from ``events.project`` (the status rollup that opens the
tail) and ``events.retrospective`` (the consuming tail past delivery).
"""

from typing import Final

INITIATIVE_REPLAN_SCHEDULED: Final[str] = "initiative.replan.scheduled"
INITIATIVE_REPLAN_STARTED: Final[str] = "initiative.replan.started"
INITIATIVE_REPLAN_COMPLETED: Final[str] = "initiative.replan.completed"
INITIATIVE_REPLAN_SKIPPED: Final[str] = "initiative.replan.skipped"
INITIATIVE_REPLAN_FAILED: Final[str] = "initiative.replan.failed"
#: The trigger refused a stall it was asked to consider, and said so to its
#: caller. Its own name because it is not a skip: nothing was collapsed and
#: nothing will retry, the initiative has no automatic route left, and the
#: caller escalates on this answer.
INITIATIVE_REPLAN_REFUSED: Final[str] = "initiative.replan.refused"
#: A person authorised one replan past the cap or the master switch. Recorded
#: apart from the scheduled event because the authority is different, and
#: "who decided this initiative should keep going" is the question the audit
#: trail is asked afterwards.
INITIATIVE_REPLAN_GRANTED: Final[str] = "initiative.replan.granted"
#: A settings read fell back to its hardcoded default. Its own name because
#: nothing was skipped: the pass continues, on a value no operator chose.
#: Riding on the skipped event made "no replan was needed" and "the settings
#: store is degraded and every replan now runs on defaults" the same signal.
INITIATIVE_REPLAN_SETTINGS_DEGRADED: Final[str] = "initiative.replan.settings_degraded"

#: A workstream finished its planned tree with at least one leaf still
#: oversized, and the extension trigger started a graft for it.
INITIATIVE_EXTENSION_CONSIDERED: Final[str] = "initiative.extension.considered"
#: The trigger refused an extension it was asked to consider, on the same two
#: guards a stalled replan can be refused on.
INITIATIVE_EXTENSION_REFUSED: Final[str] = "initiative.extension.refused"
#: The graft's own append lost a race to another writer and is retrying once
#: against the freshly read plan.
INITIATIVE_EXTENSION_VERSION_CONFLICT: Final[str] = (
    "initiative.extension.version_conflict"
)
#: An extension's items landed on the plan and its tasks were filed.
INITIATIVE_EXTENSION_GRAFTED: Final[str] = "initiative.extension.grafted"
#: An extension grafted but the driver refused to dispatch it; the plan is
#: left for the recovery sweep to pick up on its own cadence, the same as any
#: other refused drive.
INITIATIVE_EXTENSION_DRIVE_FAILED: Final[str] = "initiative.extension.drive_failed"
#: The graft itself failed (a bad decomposition, a persistence failure, or
#: both append attempts losing to a concurrent writer or a deleted plan).
INITIATIVE_EXTENSION_FAILED: Final[str] = "initiative.extension.failed"
#: An extension attempt was collapsed (already in flight) or refused before
#: starting, on the ``StageRunner`` shape ``INITIATIVE_REPLAN_SKIPPED``
#: carries for replan. Its own name, and its own ``StageRunner`` instance, so
#: an extension's own timeout or uncaught failure is never misattributed to
#: the unrelated replan mechanism the two used to share one runner with.
INITIATIVE_EXTENSION_SKIPPED: Final[str] = "initiative.extension.skipped"
#: A settings read fell back to its hardcoded default, on the same reasoning
#: ``INITIATIVE_REPLAN_SETTINGS_DEGRADED`` carries for replan.
INITIATIVE_EXTENSION_SETTINGS_DEGRADED: Final[str] = (
    "initiative.extension.settings_degraded"
)

#: An initiative with no automatic route left was put in front of a person.
#: WARNING, because it is the one signal that the organisation has stopped and
#: needs attention; everything else about that state is silent by design.
INITIATIVE_STALL_ESCALATED: Final[str] = "initiative.stall.escalated"
#: Raising the decision itself failed. The stall is unchanged and the next
#: level-triggered pass re-asks, so this reports a delayed escalation rather
#: than a lost one; it is WARNING because a store that keeps refusing means the
#: operator is never told.
INITIATIVE_STALL_ESCALATION_FAILED: Final[str] = "initiative.stall.escalation_failed"
#: A later pass found the decision already waiting. DEBUG: the operator has
#: been told, and repeating the alert every cadence is how an alert stops
#: being read.
INITIATIVE_STALL_ALREADY_OPEN: Final[str] = "initiative.stall.already_open"
#: Nothing in this deployment can ask a human, so the plan was failed with its
#: stall reason instead of being parked where nobody would ever see it.
INITIATIVE_STALL_UNDECIDABLE: Final[str] = "initiative.stall.undecidable"
#: The decision landed but the alert announcing it did not. The operator still
#: finds the item in the queue, so this reports a degraded notice rather than a
#: lost decision.
INITIATIVE_STALL_NOTICE_FAILED: Final[str] = "initiative.stall.notice_failed"
#: The operator answered, and this is what their answer did. Carries the
#: outcome, because "approved" alone does not say whether a replan actually
#: started or the plan had already moved on underneath the decision.
INITIATIVE_STALL_DECIDED: Final[str] = "initiative.stall.decided"
#: The operator said keep going and there was no longer anything able to. The
#: plan is failed rather than left reading as though a replan is coming.
INITIATIVE_STALL_DECISION_STRANDED: Final[str] = "initiative.stall.decision_stranded"
#: The decision said keep going but the decider was not a person, so the
#: operator's replan cap and switch were applied rather than lifted.
INITIATIVE_STALL_NOT_GRANTED: Final[str] = "initiative.stall.not_granted"
#: An item wearing the stalled-initiative action type that this organisation
#: did not raise. Not acted on: the action type says what a decision asks, not
#: who asked it.
INITIATIVE_STALL_FOREIGN: Final[str] = "initiative.stall.foreign"
#: The answer being acted on and the answer on the row disagree, so one of them
#: is not the decision a person took. Not acted on, and reported rather than
#: passed over, because the two can only differ if something replayed a
#: decision or wrote the row underneath it.
INITIATIVE_STALL_STALE_DECISION: Final[str] = "initiative.stall.stale_decision"

#: A person granted one more extension for a leaf the deterministic gate had
#: parked, cap and switch aside, mirroring ``INITIATIVE_REPLAN_GRANTED``.
INITIATIVE_EXTENSION_GRANTED: Final[str] = "initiative.extension.granted"
#: A workstream's extension ask crossed the deterministic autonomy gate and
#: one decision was parked for a person, mirroring
#: ``INITIATIVE_STALL_ESCALATED``.
INITIATIVE_EXTENSION_ESCALATED: Final[str] = "initiative.extension.escalated"
#: A leaf's extension ask is already settled or already open, so nothing new
#: is raised. Two call sites: ``escalate``'s own internal re-check catching
#: two concurrent recomputes racing to escalate the same leaf, and the
#: rollup's own per-leaf read finding a decision already ``PENDING`` or
#: ``REJECTED`` before it ever re-asks the trigger.
INITIATIVE_EXTENSION_ALREADY_DECIDED: Final[str] = (
    "initiative.extension.already_decided"
)
#: The decision landed but the alert announcing it did not.
INITIATIVE_EXTENSION_NOTICE_FAILED: Final[str] = "initiative.extension.notice_failed"
#: The operator answered, and this is what their answer did.
INITIATIVE_EXTENSION_DECIDED: Final[str] = "initiative.extension.decided"
#: An item wearing the extend-workstream action type that this organisation
#: did not raise. Not acted on, mirroring ``INITIATIVE_STALL_FOREIGN``.
INITIATIVE_EXTENSION_FOREIGN: Final[str] = "initiative.extension.foreign"
#: The answer being acted on and the answer on the row disagree.
INITIATIVE_EXTENSION_STALE_DECISION: Final[str] = "initiative.extension.stale_decision"
#: The decision said extend it but nothing could act on it: the workstream
#: named in its metadata no longer resolves against the plan, or no replan
#: trigger is wired to graft it.
INITIATIVE_EXTENSION_NOT_GRANTED: Final[str] = "initiative.extension.not_granted"

#: A stage's derived task id is occupied by a row the stage never minted. Read
#: as a failed attempt, so the initiative routes to a replan; named separately
#: because it is a provenance defect rather than an ordinary failed gate, and
#: the two are otherwise indistinguishable from the outside.
STAGE_TASK_ID_OCCUPIED: Final[str] = "initiative.stage.task_id_occupied"

INITIATIVE_SKELETON_SCHEDULED: Final[str] = "initiative.skeleton.scheduled"
INITIATIVE_SKELETON_STARTED: Final[str] = "initiative.skeleton.started"
INITIATIVE_SKELETON_DISPATCHED: Final[str] = "initiative.skeleton.dispatched"
INITIATIVE_SKELETON_SKIPPED: Final[str] = "initiative.skeleton.skipped"
INITIATIVE_SKELETON_FAILED: Final[str] = "initiative.skeleton.failed"
#: A settings read failed and the stage ran on its built-in default. Nothing
#: was skipped, so it is kept apart from ``_SKIPPED``: an operator alerting on
#: skips would otherwise be paged for a stage that dispatched perfectly well.
INITIATIVE_SKELETON_SETTINGS_DEGRADED: Final[str] = (
    "initiative.skeleton.settings_degraded"
)

INITIATIVE_INTEGRATION_SCHEDULED: Final[str] = "initiative.integration.scheduled"
INITIATIVE_INTEGRATION_STARTED: Final[str] = "initiative.integration.started"
INITIATIVE_INTEGRATION_DISPATCHED: Final[str] = "initiative.integration.dispatched"
INITIATIVE_INTEGRATION_SKIPPED: Final[str] = "initiative.integration.skipped"
INITIATIVE_INTEGRATION_FAILED: Final[str] = "initiative.integration.failed"
#: The assembly stage's counterpart to the skeleton one above.
INITIATIVE_INTEGRATION_SETTINGS_DEGRADED: Final[str] = (
    "initiative.integration.settings_degraded"
)

INITIATIVE_EVALUATION_SCHEDULED: Final[str] = "initiative.evaluation.scheduled"
INITIATIVE_EVALUATION_STARTED: Final[str] = "initiative.evaluation.started"
#: The objective was judged MET and the plan was written COMPLETED. Reserved
#: for that write, so an operator alerting on "evaluation completed" is not
#: paged by every unmet verdict as well.
INITIATIVE_EVALUATION_COMPLETED: Final[str] = "initiative.evaluation.completed"
#: The evaluation ran and the objective was judged UNMET. A finished judgement
#: like the one above, and an opposite outcome, so it gets its own name rather
#: than a kwarg on the completion event.
INITIATIVE_EVALUATION_UNMET: Final[str] = "initiative.evaluation.unmet"
INITIATIVE_EVALUATION_SKIPPED: Final[str] = "initiative.evaluation.skipped"
INITIATIVE_EVALUATION_FAILED: Final[str] = "initiative.evaluation.failed"
INITIATIVE_EVALUATION_RECORDED: Final[str] = "initiative.evaluation.recorded"
INITIATIVE_EVALUATION_RECORD_FAILED: Final[str] = "initiative.evaluation.record_failed"
