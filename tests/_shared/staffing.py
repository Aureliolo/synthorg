"""Test helpers for role staffing.

Both completion gates ask HR who holds their role before they run, so every
gate test needs a roster to answer with. These build one without standing up
a real registry, including the roster that answers with nobody, which is the
unstaffed case each gate treats as its own condition.
"""

from datetime import date
from unittest.mock import AsyncMock

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService
from tests._shared.ids import as_uuid
from tests._shared.mock_of import mock_of
from tests._shared.model_binding import TEST_MODEL_ID, TEST_PROVIDER


def role_holder(
    label: str,
    *,
    role: str,
    capability: CapabilityLevel | None = "capable",
    per_label_model: bool = False,
) -> AgentIdentity:
    """Build a roster agent holding *role*.

    One builder for the concept, so the identity model can change in one
    place: two builders for a role holder drift the moment a field is added.

    Args:
        label: Stable id seed and display name.
        role: The catalogued role the agent holds.
        capability: Its bound model's capability tier.
        per_label_model: Derive the bound model id from *label*, for tests
            that assert which holder's pair a verdict was recorded under.

    Returns:
        The identity.
    """
    model_id = f"example-{label}-001" if per_label_model else TEST_MODEL_ID
    return AgentIdentity(
        id=as_uuid(label),
        name=NotBlankStr(label),
        role=NotBlankStr(role),
        department=NotBlankStr("quality-assurance"),
        model=ModelConfig(
            provider=NotBlankStr(TEST_PROVIDER),
            model_id=NotBlankStr(model_id),
            capability=capability,
        ),
        hiring_date=date(2026, 1, 1),
    )


def staffing_with(
    *holders: AgentIdentity,
    executor: AgentIdentity | None = None,
) -> RoleStaffingService:
    """Return a staffing service answering with *holders* for any role.

    Args:
        *holders: The agents the roster reports. Empty means unstaffed.
        executor: The agent whose work is under review, which selection reads
            to floor the capability requirement at the author's own rung.
            ``None`` leaves the executor unknown, so the requirement stands as
            the work stated it.

    Returns:
        The service.
    """
    registry = mock_of[AgentRegistryService](
        list_by_role=AsyncMock(
            spec=AgentRegistryService.list_by_role, return_value=holders
        ),
        get=AsyncMock(spec=AgentRegistryService.get, return_value=executor),
    )
    return RoleStaffingService(registry=registry)


__all__ = ["role_holder", "staffing_with"]
