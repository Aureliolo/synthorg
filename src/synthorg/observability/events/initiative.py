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

#: An initiative with no automatic route left was put in front of a person.
#: WARNING, because it is the one signal that the organisation has stopped and
#: needs attention; everything else about that state is silent by design.
INITIATIVE_STALL_ESCALATED: Final[str] = "initiative.stall.escalated"
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

INITIATIVE_INTEGRATION_SCHEDULED: Final[str] = "initiative.integration.scheduled"
INITIATIVE_INTEGRATION_STARTED: Final[str] = "initiative.integration.started"
INITIATIVE_INTEGRATION_DISPATCHED: Final[str] = "initiative.integration.dispatched"
INITIATIVE_INTEGRATION_SKIPPED: Final[str] = "initiative.integration.skipped"
INITIATIVE_INTEGRATION_FAILED: Final[str] = "initiative.integration.failed"

INITIATIVE_EVALUATION_SCHEDULED: Final[str] = "initiative.evaluation.scheduled"
INITIATIVE_EVALUATION_STARTED: Final[str] = "initiative.evaluation.started"
INITIATIVE_EVALUATION_COMPLETED: Final[str] = "initiative.evaluation.completed"
INITIATIVE_EVALUATION_SKIPPED: Final[str] = "initiative.evaluation.skipped"
INITIATIVE_EVALUATION_FAILED: Final[str] = "initiative.evaluation.failed"
INITIATIVE_EVALUATION_RECORDED: Final[str] = "initiative.evaluation.recorded"
INITIATIVE_EVALUATION_RECORD_FAILED: Final[str] = "initiative.evaluation.record_failed"
