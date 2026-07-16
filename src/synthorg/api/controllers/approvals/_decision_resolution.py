# module-kind: code
"""Resolve an execution-time decision approval into the agent's resume input.

Turns the operator's structural pick on a decision fork into the two things
the parked agent needs: the decision reason it continues with (the chosen
option's writeup) and the structured choice recorded back onto the evidence
package. Consumed by the approvals decision controller.
"""

from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ValidationError
from synthorg.core.evidence import EvidencePackage


def resolve_decision_reason(
    item: ApprovalItem,
    *,
    chosen_option_id: str | None,
    comment: str | None,
) -> str | None:
    """Resolve the effective decision reason for an approval.

    For an execution-time decision that offers options
    (``evidence_package.options``), the operator picks by ``chosen_option_id``
    and the chosen option's writeup (``"<title>: <summary>"``) becomes the
    decision the parked agent continues with. For every other approval the
    free-text ``comment`` is used unchanged.

    Returns:
        The decision reason to record and inject on resume.

    Raises:
        ValidationError: When the approval offers options but no valid
            ``chosen_option_id`` names one of them.
    """
    evidence = item.evidence_package
    if evidence is None or not evidence.options:
        return comment
    if chosen_option_id is None:
        msg = "This decision requires choosing an option (chosen_option_id)."
        raise ValidationError(msg)
    option = next((o for o in evidence.options if o.id == chosen_option_id), None)
    if option is None:
        msg = "chosen_option_id does not name an option on this decision."
        raise ValidationError(msg)
    return f"{option.title}: {option.summary}"


def record_chosen_option(
    item: ApprovalItem, *, chosen_option_id: str | None
) -> EvidencePackage | None:
    """Return the evidence package with the operator's pick recorded, or ``None``.

    A decided decision fork must carry the structured choice, not only the
    derived free-text reason, so the dashboard renders the option the operator
    actually picked (rather than falling back to the recommended default) and an
    audit reads it without parsing prose. ``None`` for a non-decision approval,
    so the caller leaves ``evidence_package`` untouched. The id has already been
    validated against the options by :func:`resolve_decision_reason`.
    """
    evidence = item.evidence_package
    if evidence is None or not evidence.options or chosen_option_id is None:
        return None
    return evidence.model_copy(update={"chosen_option_id": chosen_option_id})
