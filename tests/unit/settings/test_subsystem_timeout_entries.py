"""Coverage for subsystem-timeout settings registered alongside the new
``simulations`` and ``client`` namespaces.

Each entry is asserted on three axes:

1. Registry presence + documented default + type.
2. Default resolution through ``SettingsService.get()``.
3. Env-variable override resolves through the standard chain.

The bounds (min/max) are part of the defended invariant: a default
that sits outside the registered bounds would fail at first read, so
asserting the default value implicitly confirms the bounds accept it.
"""

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingSource, SettingType
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


@pytest.fixture
def service() -> SettingsService:
    repo = mock_of[SettingsRepository]()
    # ``mock_of`` autospecs every method on ``SettingsRepository``; configure
    # the return values on the auto-mocked attributes rather than assigning
    # fresh ``AsyncMock`` instances (which would be bare mocks and trip
    # ``scripts/check_mock_spec.py``).
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


# (namespace, key, type, default_string, env_override_value).
# ``env_override_value`` is a stringly-typed value the resolver must
# accept; it is intentionally distinct from the default so the source
# assertion cannot be satisfied vacuously.
# A timeout governs how long a running subsystem waits, which is exactly
# the kind of knob an operator retunes against live traffic, so every
# entry here must stay live rather than compose-set.
_SUBSYSTEM_TIMEOUT_ENTRIES: tuple[tuple[str, str, SettingType, str, str], ...] = (
    ("simulations", "task_timeout_seconds", SettingType.FLOAT, "30.0", "45.0"),
    ("simulations", "review_timeout_seconds", SettingType.FLOAT, "30.0", "45.0"),
    ("client", "human_response_timeout_seconds", SettingType.FLOAT, "60.0", "120.0"),
    ("tools", "web_request_timeout_seconds", SettingType.FLOAT, "30.0", "45.0"),
    ("tools", "git_command_timeout_seconds", SettingType.FLOAT, "60.0", "120.0"),
    (
        "communication",
        "escalation_subscriber_reconnect_delay_seconds",
        SettingType.FLOAT,
        "1.0",
        "2.5",
    ),
    ("engine", "shutdown_tool_timeout_seconds", SettingType.FLOAT, "60.0", "120.0"),
    ("security", "timeout_check_interval_seconds", SettingType.FLOAT, "60.0", "30.0"),
    (
        "integrations",
        "oauth_device_flow_poll_interval_seconds",
        SettingType.INTEGER,
        "5",
        "10",
    ),
)


@pytest.mark.parametrize(
    ("namespace", "key", "expected_type", "expected_default", "_env_value"),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
def test_subsystem_timeout_entry_registered(
    namespace: str,
    key: str,
    expected_type: SettingType,
    expected_default: str,
    _env_value: str,
) -> None:
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"setting {namespace}.{key} missing from registry"
    assert defn.type is expected_type
    assert defn.default == expected_default
    assert defn.compose_set is False


@pytest.mark.parametrize(
    ("namespace", "key", "_expected_type", "expected_default", "_env_value"),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
async def test_subsystem_timeout_entry_default_resolves(
    namespace: str,
    key: str,
    _expected_type: SettingType,
    expected_default: str,
    _env_value: str,
    service: SettingsService,
) -> None:
    result = await service.get(namespace, key)
    assert result.value == expected_default
    assert result.source is SettingSource.DEFAULT


@pytest.mark.parametrize(
    ("namespace", "key", "_expected_type", "_expected_default", "env_value"),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
async def test_subsystem_timeout_entry_env_override(
    namespace: str,
    key: str,
    _expected_type: SettingType,
    _expected_default: str,
    env_value: str,
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_var = f"SYNTHORG_{namespace.upper()}_{key.upper()}"
    monkeypatch.setenv(env_var, env_value)
    result = await service.get(namespace, key)
    assert result.value == env_value
    assert result.source is SettingSource.ENVIRONMENT
