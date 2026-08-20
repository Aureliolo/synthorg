# module-kind: code
"""The output-style guard for the prose a charter interview produces.

Both halves of an interview turn are agent output the organisation keeps or
sends. The question goes straight to the operator in the chat transcript, which
is the surface they read most; the draft becomes the persisted charter, and its
``proposed_project_name`` becomes the name of the project the whole run is
delivered under. A live run named a project "Falling Blocks [em-dash] Browser
Puzzle Game" and asked two of its five questions with the one character the
policy ships a hard rule against, because neither was a declared boundary.

It sits on the parse of the model's reply, next to the schema check and for the
same reason the plan guard sits on the submit path: the producer is still there
and the strategy already asks it to correct a refused reply, so a refusal costs
one repair turn rather than the interview.

The question takes the MESSAGE channel and the draft the DELIVERABLE one: one
is addressed to a person, the other is an artefact they approve, and the
channel decides how the segmenter reads code spans inside prose.
"""

from pydantic import ValidationError as PydanticValidationError

from synthorg.engine.output_style import (
    OutputChannel,
    OutputContext,
    approve_texts,
)
from synthorg.meta.charter.models import CharterDraft, InterviewDecision
from synthorg.observability import safe_error_description

_QUESTION_CONTEXT: OutputContext = OutputContext(channel=OutputChannel.MESSAGE)
_DRAFT_CONTEXT: OutputContext = OutputContext(channel=OutputChannel.DELIVERABLE)

#: Appended to a style refusal so the repair turn asks for the right thing. A
#: schema refusal wants the same content in a different shape; this one wants
#: the content itself reworded, and asking for a re-send would get the same
#: characters back.
_REWORD = " Reword it and send the whole reply again."


def _refusal(reason: str) -> str:
    """Phrase a style rejection for the model that can still fix it.

    Args:
        reason: The policy's own summary of what blocked.

    Returns:
        A sentence naming the rule and what to do about it.
    """
    return f"The wording breaks a house style rule: {reason}{_REWORD}"


def _approved_draft(draft: CharterDraft) -> tuple[CharterDraft | None, str]:
    """Approve every piece of prose the draft carries.

    Args:
        draft: The charter the interview drafted.

    Returns:
        The draft carrying any auto-rewrite, and an empty string; or ``None``
        and the refusal when a hard rule blocked.
    """
    scalars = approve_texts(
        (
            draft.title,
            draft.brief,
            draft.proposed_project_name or "",
            draft.proposed_project_description,
        ),
        _DRAFT_CONTEXT,
    )
    if scalars.refusal is not None:
        return None, _refusal(scalars.refusal)
    sequences: list[tuple[str, ...]] = []
    for texts in (
        draft.goals,
        draft.constraints,
        draft.success_criteria,
        draft.scope.in_scope,
        draft.scope.out_of_scope,
    ):
        approval = approve_texts(texts, _DRAFT_CONTEXT)
        if approval.refusal is not None:
            return None, _refusal(approval.refusal)
        sequences.append(approval.texts)
    title, brief, project_name, project_description = scalars.texts
    goals, constraints, success_criteria, in_scope, out_of_scope = sequences
    return (
        draft.model_copy(
            update={
                "title": title,
                "brief": brief,
                # The XOR with ``project_id`` is decided by whether a name was
                # proposed at all, so an absent one stays absent: the empty
                # string handed to the approval is a placeholder, never a value.
                "proposed_project_name": (
                    project_name if draft.proposed_project_name is not None else None
                ),
                "proposed_project_description": project_description,
                "goals": goals,
                "constraints": constraints,
                "success_criteria": success_criteria,
                "scope": draft.scope.model_copy(
                    update={"in_scope": in_scope, "out_of_scope": out_of_scope}
                ),
            }
        ),
        "",
    )


def approved_decision(
    decision: InterviewDecision,
) -> tuple[InterviewDecision | None, str]:
    """Return *decision* fit to send and keep, or the reason it is not.

    Args:
        decision: The parsed interview turn, before anything is shown or saved.

    Returns:
        The decision carrying any auto-rewrite, and an empty string; or ``None``
        and the refusal to put to the model as its repair turn.
    """
    if decision.next_question is not None:
        approval = approve_texts((decision.next_question,), _QUESTION_CONTEXT)
        if approval.refusal is not None:
            return None, _refusal(approval.refusal)
        return _revalidated(decision, {"next_question": approval.texts[0]})
    if decision.draft is None:
        return decision, ""
    draft, refusal = _approved_draft(decision.draft)
    if draft is None:
        return None, refusal
    return _revalidated(decision, {"draft": draft})


def _revalidated(
    decision: InterviewDecision, update: dict[str, object]
) -> tuple[InterviewDecision | None, str]:
    """Re-judge a rewritten decision on the text it now carries.

    ``model_copy(update=...)`` does not validate, and ``NotBlankStr`` only runs
    inside a model, so a rule whose replacement empties a span would otherwise
    land blank prose in front of an operator.

    Args:
        decision: The decision as parsed.
        update: The approved fields to substitute.

    Returns:
        The re-validated decision and an empty string, or ``None`` and the
        reason the rewrite left it invalid.
    """
    rewritten = decision.model_copy(update=update)
    try:
        return InterviewDecision.model_validate(rewritten.model_dump()), ""
    except PydanticValidationError as exc:
        return None, (
            "The house style rewrite left the reply invalid: "
            f"{safe_error_description(exc)}"
        )


__all__ = ["approved_decision"]
