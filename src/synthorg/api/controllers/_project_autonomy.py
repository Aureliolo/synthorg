"""Setting an initiative's operator-chosen oversight mode.

The per-initiative autonomy mode carries a CEO-only deliberate opt-in for the
gate-disabling ``full`` transition plus a dedicated audit event, and the write
itself lives here too: turning an initiative to ``full`` turns the approval
gate OFF for every agent working it, which is not a field write dressed as one.
"""

from litestar import Request, Response
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api._read_names import agent_name_map
from synthorg.api.channels import CHANNEL_PROJECTS, publish_ws_event
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_named_rows import ProjectRow
from synthorg.api.guards import role_of
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.project_service import ProjectService
from synthorg.api.ws_models import WsEventType
from synthorg.core.auth.roles import HumanRole
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import (
    ForbiddenError,
    ValidationError,
    VersionConflictError,
)
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_PROJECT_AUTONOMY_MODE_CHANGED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.settings.state import config_resolver_of

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


class AutonomyModeTransition(BaseModel):
    """An initiative autonomy-mode change, carrying override + effective modes.

    Attributes:
        previous: The operator-set override before the change (``None`` means
            "inherit the default").
        new: The operator-set override after the change (``None`` means
            "inherit").
        effective_previous: What the initiative resolved to before the change,
            with inheritance applied.
        effective_new: What the initiative resolves to after the change, with
            inheritance applied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    previous: AutonomyLevel | None
    new: AutonomyLevel | None
    effective_previous: AutonomyLevel
    effective_new: AutonomyLevel

    @property
    def gate_disabled(self) -> bool:
        """Whether the effective post-change mode leaves the gate off (full)."""
        return self.effective_new is AutonomyLevel.FULL

    @property
    def newly_gate_off(self) -> bool:
        """Whether the change NEWLY enters full (gate-off) from a gated tier."""
        return self.gate_disabled and self.effective_previous is not AutonomyLevel.FULL


def guard_full_autonomy_optin(
    *,
    role: HumanRole | None,
    transition: AutonomyModeTransition,
    confirm: bool,
) -> None:
    """Gate the security-weakening opt-in to full (gate-off) autonomy.

    The guard keys off the EFFECTIVE resolved modes, so clearing an override
    that inherits ``full`` is guarded the same as setting ``full`` outright.
    Reaching an effective ``full`` from a gated tier disables the per-action
    SecOps gate for the initiative's agents, so it is a deliberate, CEO-only
    action: any other role, or a missing ``confirm``, is rejected. Tightening
    or lateral transitions are unguarded.

    Raises:
        ForbiddenError: A non-CEO role attempted the transition to full.
        ValidationError: The transition to full lacked ``confirm=true``.
    """
    if not transition.newly_gate_off:
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
    transition: AutonomyModeTransition,
    requested_by: str,
) -> None:
    """Audit an initiative autonomy-mode transition after the write lands.

    ``gate_disabled`` is keyed off the EFFECTIVE result, so clearing an
    override that inherits ``full`` is recorded as gate-off rather than
    mislabelled from the ``None`` literal. A transition that NEWLY enters
    gate-off is logged at WARNING so it stands out from a routine tightening;
    every other transition is INFO. Carries the actor plus the override and
    effective modes so an incident review can attribute who set what.
    """
    previous = transition.previous
    new = transition.new
    emit = logger.warning if transition.newly_gate_off else logger.info
    emit(
        API_PROJECT_AUTONOMY_MODE_CHANGED,
        project_id=project_id,
        previous_mode=previous.value if previous is not None else None,
        new_mode=new.value if new is not None else None,
        effective_mode=transition.effective_new.value,
        requested_by=requested_by,
        gate_disabled=transition.gate_disabled,
    )


async def apply_autonomy_mode(
    request: Request[object, object, State],
    state: State,
    service: ProjectService,
    project_id: str,
    data: ProjectAutonomyModeRequest,
) -> Response[ApiResponse[ProjectRow]]:
    """Set (or clear) *project_id*'s operator-set autonomy mode.

    Args:
        request: The incoming request (carries the acting role + user).
        state: Application state.
        service: The project service the route already built.
        project_id: Project identifier.
        data: Autonomy-mode payload.

    Returns:
        The updated project.

    Raises:
        ForbiddenError: A non-CEO attempted the transition to full.
        ValidationError: The transition to full lacked confirmation.
        VersionConflictError: A concurrent write moved the version.
    """
    project = require_resource_or_404(
        await service.get(project_id),
        resource_type="Project",
        identifier=project_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="update",
    )
    previous_mode = project.autonomy_mode
    # Clearing an override (mode=None) inherits the company default, which
    # can itself be full (gate-off). Guard and audit the EFFECTIVE resolved
    # mode so a clear-into-inherited-full can neither bypass the CEO opt-in
    # nor be mislabelled as gate-on. Department overrides are not yet a
    # resolution input, so the effective fallback is the company default.
    company_default = await config_resolver_of(state.app_state).get_enum(
        "company", "autonomy_level", AutonomyLevel
    )
    transition = AutonomyModeTransition(
        previous=previous_mode,
        new=data.mode,
        effective_previous=(
            previous_mode if previous_mode is not None else company_default
        ),
        effective_new=data.mode if data.mode is not None else company_default,
    )
    guard_full_autonomy_optin(
        role=role_of(request),
        transition=transition,
        confirm=data.confirm,
    )
    updated = project.model_copy(
        update={"autonomy_mode": data.mode, "version": project.version + 1},
    )
    expected = (
        data.expected_version if data.expected_version is not None else project.version
    )
    try:
        await service.update(updated, expected_version=expected)
    except PersistenceVersionConflictError as exc:
        msg = f"Project {project_id!r} was modified concurrently"
        raise VersionConflictError(msg) from exc
    audit_autonomy_mode_change(
        project_id=project_id,
        transition=transition,
        requested_by=extract_requester(),
    )
    publish_ws_event(
        request,
        WsEventType.PROJECT_AUTONOMY_MODE_CHANGED,
        CHANNEL_PROJECTS,
        {
            "project_id": project_id,
            "new_mode": data.mode.value if data.mode is not None else None,
            "previous_mode": (
                previous_mode.value if previous_mode is not None else None
            ),
            "new_version": updated.version,
        },
    )
    return Response(
        content=ApiResponse[ProjectRow](
            data=ProjectRow.of(updated, await agent_name_map(state.app_state))
        ),
        status_code=200,
    )
