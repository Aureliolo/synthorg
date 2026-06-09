"""Unit tests for ConfigResolver structural data accessors.

Tests for ``get_json``, ``get_agents``, ``get_departments``, and
``get_provider_configs`` -- extracted from ``test_resolver.py`` to
keep files under the 800-line limit.
"""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver
from tests.unit.settings.conftest import (
    FakeAgentConfig,
    FakeDepartment,
    FakeProviderConfig,
    make_setting_value,
)

_make_value = make_setting_value


class _FakeRootConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    agents: tuple[FakeAgentConfig, ...] = ()
    departments: tuple[FakeDepartment, ...] = ()
    providers: dict[str, FakeProviderConfig] = {}


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def mock_settings() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def root_config() -> _FakeRootConfig:
    return _FakeRootConfig()


@pytest.fixture
def resolver(mock_settings: AsyncMock, root_config: _FakeRootConfig) -> ConfigResolver:
    return ConfigResolver(
        settings_service=mock_settings,
        config=root_config,  # type: ignore[arg-type]
    )


# ── JSON Accessor Tests ──────────────────────────────────────────


@pytest.mark.unit
class TestGetJson:
    """Tests for get_json() generic accessor."""

    async def test_valid_json_array(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value('[{"name": "a"}]')
        result = await resolver.get_json("company", "agents")
        assert result == [{"name": "a"}]
        mock_settings.get.assert_awaited_once_with("company", "agents")

    async def test_valid_json_object(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value('{"k": "v"}')
        result = await resolver.get_json("test", "key")
        assert result == {"k": "v"}

    async def test_invalid_json_raises_value_error(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("not-json{}")
        with pytest.raises(ValueError, match="invalid JSON"):
            await resolver.get_json("test", "key")

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_json("bad", "key")

    async def test_empty_array(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("[]")
        assert await resolver.get_json("test", "key") == []


# ── Composed Read: Agents ────────────────────────────────────────


@pytest.mark.unit
class TestGetAgents:
    """Tests for get_agents() composed read."""

    async def test_json_roundtrip(self, mock_settings: AsyncMock) -> None:
        """Agent configs parsed from JSON setting."""
        from synthorg.config.agent_schema import AgentConfig

        agent_data = [
            {"name": "alice", "role": "dev", "department": "eng"},
            {"name": "bob", "role": "qa", "department": "eng"},
        ]
        mock_settings.get.return_value = _make_value(
            json.dumps(agent_data),
            namespace=SettingNamespace.COMPANY,
            key="agents",
        )
        config = _FakeRootConfig()
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_agents()

        assert len(result) == 2
        assert isinstance(result[0], AgentConfig)
        assert result[0].name == "alice"
        assert result[1].name == "bob"

    async def test_empty_list_is_valid_override(self, mock_settings: AsyncMock) -> None:
        """Empty JSON list is a valid override returning empty tuple."""
        mock_settings.get.return_value = _make_value(
            "[]",
            namespace=SettingNamespace.COMPANY,
            key="agents",
        )
        agent = FakeAgentConfig(name="fallback-agent")
        config = _FakeRootConfig(agents=(agent,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_agents()

        assert result == ()

    async def test_invalid_json_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """Invalid JSON -> fall back to config.agents."""
        mock_settings.get.return_value = _make_value(
            "not-json",
            namespace=SettingNamespace.COMPANY,
            key="agents",
        )
        agent = FakeAgentConfig(name="safe-agent")
        config = _FakeRootConfig(agents=(agent,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_agents()

        assert len(result) == 1
        assert result[0].name == "safe-agent"

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_agents()

    async def test_invalid_schema_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """Valid JSON but invalid AgentConfig schema -> fall back."""
        mock_settings.get.return_value = _make_value(
            '[{"not_a_valid_field": "value"}]',
            namespace=SettingNamespace.COMPANY,
            key="agents",
        )
        agent = FakeAgentConfig(name="schema-fallback")
        config = _FakeRootConfig(agents=(agent,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_agents()

        assert len(result) == 1
        assert result[0].name == "schema-fallback"

    async def test_wrong_json_shape_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """JSON dict instead of list -> fall back."""
        mock_settings.get.return_value = _make_value(
            '{"name": "alice"}',
            namespace=SettingNamespace.COMPANY,
            key="agents",
        )
        agent = FakeAgentConfig(name="shape-fallback")
        config = _FakeRootConfig(agents=(agent,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_agents()

        assert len(result) == 1
        assert result[0].name == "shape-fallback"


# ── Composed Read: Departments ───────────────────────────────────


@pytest.mark.unit
class TestGetDepartments:
    """Tests for get_departments() composed read."""

    async def test_json_roundtrip(self, mock_settings: AsyncMock) -> None:
        """Departments parsed from JSON setting."""
        from synthorg.core.company_departments import Department

        dept_data = [
            {"name": "engineering", "head": "alice"},
        ]
        mock_settings.get.return_value = _make_value(
            json.dumps(dept_data),
            namespace=SettingNamespace.COMPANY,
            key="departments",
        )
        config = _FakeRootConfig()
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_departments()

        assert len(result) == 1
        assert isinstance(result[0], Department)
        assert result[0].name == "engineering"

    async def test_empty_list_is_valid_override(self, mock_settings: AsyncMock) -> None:
        """Empty JSON list is a valid override returning empty tuple."""
        mock_settings.get.return_value = _make_value(
            "[]",
            namespace=SettingNamespace.COMPANY,
            key="departments",
        )
        dept = FakeDepartment(name="fallback-dept")
        config = _FakeRootConfig(departments=(dept,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_departments()

        assert result == ()

    async def test_invalid_json_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value(
            "{bad-json",
            namespace=SettingNamespace.COMPANY,
            key="departments",
        )
        dept = FakeDepartment(name="safe-dept")
        config = _FakeRootConfig(departments=(dept,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_departments()

        assert len(result) == 1
        assert result[0].name == "safe-dept"

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_departments()

    async def test_invalid_schema_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """Valid JSON but invalid Department schema -> fall back."""
        mock_settings.get.return_value = _make_value(
            '[{"bad_field": "value"}]',
            namespace=SettingNamespace.COMPANY,
            key="departments",
        )
        dept = FakeDepartment(name="schema-dept")
        config = _FakeRootConfig(departments=(dept,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_departments()

        assert len(result) == 1
        assert result[0].name == "schema-dept"

    async def test_wrong_json_shape_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """JSON dict instead of list -> fall back."""
        mock_settings.get.return_value = _make_value(
            '{"name": "eng"}',
            namespace=SettingNamespace.COMPANY,
            key="departments",
        )
        dept = FakeDepartment(name="shape-dept")
        config = _FakeRootConfig(departments=(dept,))
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_departments()

        assert len(result) == 1
        assert result[0].name == "shape-dept"


# ── Composed Read: Provider Configs ──────────────────────────────


@pytest.mark.unit
class TestGetProviderConfigs:
    """Tests for get_provider_configs() composed read."""

    async def test_json_roundtrip(self, mock_settings: AsyncMock) -> None:
        """Provider configs parsed from JSON setting."""
        from synthorg.config.schema import ProviderConfig

        prov_data = {
            "test-provider": {"driver": "litellm"},
        }
        mock_settings.get.return_value = _make_value(
            json.dumps(prov_data),
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        config = _FakeRootConfig()
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        assert "test-provider" in result
        assert isinstance(result["test-provider"], ProviderConfig)
        assert result["test-provider"].driver == "litellm"

    async def test_empty_dict_is_valid_override(self, mock_settings: AsyncMock) -> None:
        """Empty JSON dict is a valid override returning empty dict."""
        mock_settings.get.return_value = _make_value(
            "{}",
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        config = _FakeRootConfig(
            providers={
                "fallback": FakeProviderConfig(driver="test-driver"),
            },
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        assert result == {}

    async def test_invalid_json_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value(
            "not-valid-json",
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        config = _FakeRootConfig(
            providers={"safe": FakeProviderConfig()},
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        assert "safe" in result

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_provider_configs()

    async def test_invalid_schema_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """Valid JSON but invalid ProviderConfig schema -> fall back."""
        mock_settings.get.return_value = _make_value(
            '{"bad": {"driver": ""}}',
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        config = _FakeRootConfig(
            providers={"safe": FakeProviderConfig()},
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        assert "safe" in result

    async def test_wrong_json_shape_falls_back_to_config(
        self, mock_settings: AsyncMock
    ) -> None:
        """JSON list instead of dict -> fall back."""
        mock_settings.get.return_value = _make_value(
            '[{"driver": "litellm"}]',
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        config = _FakeRootConfig(
            providers={"shape-safe": FakeProviderConfig()},
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        assert "shape-safe" in result

    async def test_fallback_returns_immutable_mapping(
        self, mock_settings: AsyncMock
    ) -> None:
        """Returned mapping must be an immutable MappingProxyType view."""
        from types import MappingProxyType

        mock_settings.get.return_value = _make_value(
            "null",
            namespace=SettingNamespace.PROVIDERS,
            key="configs",
        )
        prov = FakeProviderConfig(driver="original")
        config = _FakeRootConfig(providers={"p": prov})
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=config,  # type: ignore[arg-type]
        )
        result = await resolver.get_provider_configs()

        # Concrete type guarantees cannot silently regress to a mutable dict.
        assert isinstance(result, MappingProxyType)

        # Every mutation pathway must raise TypeError.
        with pytest.raises(TypeError):
            result["injected"] = FakeProviderConfig(driver="evil")  # type: ignore[index]
        with pytest.raises(TypeError):
            del result["p"]  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            result.pop("p")  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            result.clear()  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            result.update({"x": FakeProviderConfig()})  # type: ignore[attr-defined]

        fresh = await resolver.get_provider_configs()
        assert "injected" not in fresh
        assert "p" in fresh
