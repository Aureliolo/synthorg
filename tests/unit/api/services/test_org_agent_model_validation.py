"""Tests for post-setup agent-model catalog validation on the PATCH path.

``OrgAgentMutationsMixin._validate_agent_update`` now rejects a model
reassignment that names a provider/model the live catalogue no longer
exposes, closing the gap the setup endpoint already guarded.
"""

from collections.abc import Mapping
from typing import override

import pytest

from synthorg.api.services._org_agent_mutations import OrgAgentMutationsMixin
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.organization.models import UpdateAgentOrgRequest

pytestmark = pytest.mark.unit

_PROVIDERS: Mapping[str, ProviderConfig] = {
    "example-provider": ProviderConfig(
        connection_name="conn-test",
        models=(ProviderModelConfig(id="example-large-002", metadata=ModelMetadata()),),
    ),
}


class _Harness(OrgAgentMutationsMixin):
    """Minimal mixin host exposing only the validation seam."""

    @override
    async def _read_provider_configs(self) -> Mapping[str, ProviderConfig]:
        return _PROVIDERS


async def _validate(provider: str, model_id: str) -> dict[str, object]:
    data = UpdateAgentOrgRequest(model_provider=provider, model_id=model_id)
    return await _Harness()._validate_agent_update("writer", data, ())


class TestPatchAgentModelValidation:
    async def test_valid_pair_passes(self) -> None:
        updates = await _validate("example-provider", "example-large-002")
        assert updates["model_provider"] == "example-provider"
        assert updates["model_id"] == "example-large-002"

    async def test_unknown_provider_raises_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            await _validate("ghost-provider", "example-large-002")

    async def test_absent_model_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            await _validate("example-provider", "no-such-model")

    async def test_no_model_fields_skips_validation(self) -> None:
        # An update touching only the role must not require provider configs.
        data = UpdateAgentOrgRequest(role="Engineer")
        updates = await _Harness()._validate_agent_update("writer", data, ())
        assert updates == {"role": "Engineer"}
