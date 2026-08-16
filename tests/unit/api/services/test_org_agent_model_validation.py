"""Tests for post-setup agent-model catalog validation on the PATCH path.

``OrgAgentMutationsMixin._validate_agent_update`` now rejects a model
reassignment that names a provider/model the live catalogue no longer
exposes, closing the gap the setup endpoint already guarded.
"""

from collections.abc import Mapping
from typing import override

import pytest

from synthorg.api.services._org_agent_mutations import OrgAgentMutationsMixin
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.clock import Clock
from synthorg.core.company_departments import Department
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.hr.registry import AgentRegistryService
from synthorg.organization.models import UpdateAgentOrgRequest
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_PROVIDERS: Mapping[str, ProviderConfig] = {
    "example-provider": ProviderConfig(
        connection_name="conn-test",
        models=(
            ProviderModelConfig(id="example-expert-002", metadata=ModelMetadata()),
            ProviderModelConfig(id="example-basic-001", metadata=ModelMetadata()),
        ),
    ),
}


class _Harness(OrgAgentMutationsMixin):
    """Minimal mixin host exposing only the validation seam.

    The CRUD/snapshot seams satisfy the abstract contract with inert
    bodies: these tests exercise only ``_validate_agent_update``, which
    reaches ``_read_provider_configs``.
    """

    @override
    async def _read_provider_configs(self) -> Mapping[str, ProviderConfig]:
        return _PROVIDERS

    @override
    async def _read_setting_versioned(
        self, namespace: str, key: str
    ) -> tuple[str, str]:
        return ("", "")

    @override
    async def _read_departments(self) -> tuple[Department, ...]:
        return ()

    @override
    async def _read_agents(self) -> tuple[AgentConfig, ...]:
        return ()

    @override
    async def _write_agents(
        self,
        agents: tuple[AgentConfig, ...],
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        return None

    @override
    async def _snapshot_company(self) -> None:
        return None

    @override
    def _live_agent_registry(self) -> AgentRegistryService | None:
        return None

    @override
    def _roster_clock(self) -> Clock:
        return FakeClock()

    @override
    def _find_department(
        self, departments: tuple[Department, ...], name: str
    ) -> Department | None:
        return None

    @override
    def _find_agent(
        self, agents: tuple[AgentConfig, ...], name: str
    ) -> AgentConfig | None:
        return None

    @override
    def _validate_permutation(
        self,
        current_names: tuple[str, ...],
        requested_names: tuple[str, ...],
        entity: str,
    ) -> None:
        return None


async def _validate(
    provider: str,
    model_id: str,
    existing: AgentConfig | None = None,
) -> dict[str, object]:
    data = UpdateAgentOrgRequest(model_provider=provider, model_id=model_id)
    return await _Harness()._validate_agent_update("writer", data, (), existing)


class TestPatchAgentModelValidation:
    async def test_valid_pair_builds_nested_model(self) -> None:
        # The reassignment must land in the nested ``model`` dict (keyed
        # ``provider`` / ``model_id``), not as flat fields that serialisation
        # would drop -- otherwise the update silently no-ops.
        updates = await _validate("example-provider", "example-expert-002")
        assert updates["model"] == {
            "provider": "example-provider",
            "model_id": "example-expert-002",
        }
        assert "model_provider" not in updates
        assert "model_id" not in updates

    async def test_reassignment_preserves_sibling_model_keys(self) -> None:
        existing = AgentConfig(
            name="writer",
            role="Writer",
            department="eng",
            model={
                "provider": "example-provider",
                "model_id": "example-expert-001",
                "temperature": 0.3,
            },
        )
        updates = await _validate(
            "example-provider", "example-expert-002", existing=existing
        )
        assert updates["model"] == {
            "provider": "example-provider",
            "model_id": "example-expert-002",
            "temperature": 0.3,
        }

    async def test_reassignment_drops_the_previous_pairs_rung(self) -> None:
        # The rung describes the PAIR. Carried across a repoint it becomes a
        # claim about a model this agent no longer runs, and the one control
        # an operator has for changing the binding is what writes it.
        existing = AgentConfig(
            name="writer",
            role="Writer",
            department="eng",
            model={
                "provider": "example-provider",
                "model_id": "example-expert-001",
                "capability": "expert",
            },
        )
        updates = await _validate(
            "example-provider", "example-basic-001", existing=existing
        )
        assert updates["model"] == {
            "provider": "example-provider",
            "model_id": "example-basic-001",
        }

    async def test_unknown_provider_raises_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            await _validate("ghost-provider", "example-expert-002")

    async def test_absent_model_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            await _validate("example-provider", "no-such-model")

    async def test_no_model_fields_skips_validation(self) -> None:
        # An update touching only the role must not require provider configs.
        data = UpdateAgentOrgRequest(role="Engineer")
        updates = await _Harness()._validate_agent_update("writer", data, ())
        assert updates == {"role": "Engineer"}
