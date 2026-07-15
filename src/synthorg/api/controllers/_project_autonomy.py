"""Autonomy-mode request DTO, opt-in guard, and audit for the projects API.

The per-initiative operator-set autonomy mode carries a CEO-only deliberate
opt-in for the gate-disabling ``full`` transition plus a dedicated audit
event, kept beside the controller so the handler stays a thin seam.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.auth.roles import HumanRole
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import ForbiddenError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_PROJECT_AUTONOMY_MODE_CHANGED

logger = get_logger(__name__)


class ProjectAutonomyModeRequest(BaseModel):
    """Request body for setting an initiative's operator-set autonomy mode.

    Attributes:
        mode: The oversight mode the SecOps gate resolves against for this
            initiative; ``null`` clears the override so the initiative
            inherits the department or company autonomy default.
        confirm: Deliberate-action flag. Setting an initiative to ``full``
            (gate-off pass-through) is a security-weakening transition and
            requires ``confirm=true`` (and the CEO role); ignored otherwise.
        expected_version: Optional optimistic-concurrency guard. When set,
            the write lands only if the project's stored version still
            matches, so a concurrent edit cannot be silently clobbered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    mode: AutonomyLevel | None = Field(
        default=None,
        description="Operator-set oversight mode (null inherits the default)",
    )
    confirm: bool = Field(
        default=False,
        description="Deliberate opt-in required to set an initiative to full",
    )
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Optimistic-concurrency guard; write lands only if version matches",
    )


def _is_optin_to_full(
    previous: AutonomyLevel | None, new: AutonomyLevel | None
) -> bool:
    """Whether ``previous -> new`` is a transition INTO full (gate-off).

    Returns:
        ``True`` when *new* is ``full`` and *previous* was not.
    """
    return new is AutonomyLevel.FULL and previous is not AutonomyLevel.FULL


def guard_full_autonomy_optin(
    *,
    role: HumanRole | None,
    previous: AutonomyLevel | None,
    new: AutonomyLevel | None,
    confirm: bool,
) -> None:
    """Gate the security-weakening opt-in to full (gate-off) autonomy.

    Setting an initiative to ``full`` disables the per-action SecOps gate
    for its agents, so a transition INTO ``full`` is a deliberate,
    CEO-only action: any other role, or a missing ``confirm``, is rejected.
    Tightening or lateral transitions are unguarded.

    Raises:
        ForbiddenError: A non-CEO role attempted the transition to full.
        ValidationError: The transition to full lacked ``confirm=true``.
    """
    if not _is_optin_to_full(previous, new):
        return
    if role is not HumanRole.CEO:
        logger.warning(
            API_PROJECT_AUTONOMY_MODE_CHANGED,
            reason="full_optin_denied_non_ceo",
            role=role.value if role is not None else None,
        )
        msg = "Only the CEO may set an initiative to full (gate-off) autonomy"
        raise ForbiddenError(msg)
    if not confirm:
        msg = (
            "Setting an initiative to full (gate-off) autonomy is a "
            "security-weakening action and requires confirm=true"
        )
        raise ValidationError(msg)


def audit_autonomy_mode_change(
    *,
    project_id: str,
    previous: AutonomyLevel | None,
    new: AutonomyLevel | None,
    requested_by: str,
) -> None:
    """Audit an initiative autonomy-mode transition after the write lands.

    A transition INTO ``full`` (gate-off) is logged at WARNING so a
    gate-disabling opt-in stands out in the audit stream from a routine
    tightening; every other transition is INFO. Carries the actor and the
    from/to modes so an incident review can attribute who set what.
    """
    to_full = _is_optin_to_full(previous, new)
    emit = logger.warning if to_full else logger.info
    emit(
        API_PROJECT_AUTONOMY_MODE_CHANGED,
        project_id=project_id,
        previous_mode=previous.value if previous is not None else None,
        new_mode=new.value if new is not None else None,
        requested_by=requested_by,
        gate_disabled=to_full,
    )
