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

from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingSource, SettingType
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    # ``spec=`` already auto-mocks every method on ``SettingsRepository``;
    # configure the return values via the auto-mocked attributes rather
    # than assigning fresh ``AsyncMock`` instances (which would be bare
    # mocks and trip ``scripts/check_mock_spec.py``).
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


# (namespace, key, type, default_string, env_override_value, read_only_post_init).
# ``env_override_value`` is a stringly-typed value the resolver must
# accept; it is intentionally distinct from the default so the source
# assertion cannot be satisfied vacuously.
# ``read_only_post_init`` is True only for entries that ``RootConfig``
# / a bridge config snapshots once at startup -- mutating them via
# ``SettingsService.set()`` would silently misrepresent runtime state
# until the next restart, so the registry rejects the write.
_SUBSYSTEM_TIMEOUT_ENTRIES: tuple[tuple[str, str, SettingType, str, str, bool], ...] = (
    ("simulations", "task_timeout_seconds", SettingType.FLOAT, "30.0", "45.0", False),
    (
        "simulations",
        "review_timeout_seconds",
        SettingType.FLOAT,
        "30.0",
        "45.0",
        False,
    ),
    (
        "client",
        "human_response_timeout_seconds",
        SettingType.FLOAT,
        "60.0",
        "120.0",
        False,
    ),
    ("tools", "web_request_timeout_seconds", SettingType.FLOAT, "30.0", "45.0", False),
    (
        "tools",
        "git_command_timeout_seconds",
        SettingType.FLOAT,
        "60.0",
        "120.0",
        False,
    ),
    (
        "communication",
        "escalation_subscriber_reconnect_delay_seconds",
        SettingType.FLOAT,
        "1.0",
        "2.5",
        True,
    ),
    (
        "engine",
        "shutdown_tool_timeout_seconds",
        SettingType.FLOAT,
        "60.0",
        "120.0",
        False,
    ),
    (
        "security",
        "timeout_check_interval_seconds",
        SettingType.FLOAT,
        "60.0",
        "30.0",
        False,
    ),
    (
        "integrations",
        "oauth_device_flow_poll_interval_seconds",
        SettingType.INTEGER,
        "5",
        "10",
        False,
    ),
)


@pytest.mark.parametrize(
    (
        "namespace",
        "key",
        "expected_type",
        "expected_default",
        "_env_value",
        "expected_read_only_post_init",
    ),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
def test_subsystem_timeout_entry_registered(
    namespace: str,
    key: str,
    expected_type: SettingType,
    expected_default: str,
    _env_value: str,
    expected_read_only_post_init: bool,
) -> None:
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"setting {namespace}.{key} missing from registry"
    assert defn.type is expected_type
    assert defn.default == expected_default
    assert defn.read_only_post_init is expected_read_only_post_init


@pytest.mark.parametrize(
    (
        "namespace",
        "key",
        "_expected_type",
        "expected_default",
        "_env_value",
        "_expected_read_only_post_init",
    ),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
async def test_subsystem_timeout_entry_default_resolves(
    namespace: str,
    key: str,
    _expected_type: SettingType,
    expected_default: str,
    _env_value: str,
    _expected_read_only_post_init: bool,
    service: SettingsService,
) -> None:
    result = await service.get(namespace, key)
    assert result.value == expected_default
    assert result.source is SettingSource.DEFAULT


@pytest.mark.parametrize(
    (
        "namespace",
        "key",
        "_expected_type",
        "_expected_default",
        "env_value",
        "_expected_read_only_post_init",
    ),
    _SUBSYSTEM_TIMEOUT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _SUBSYSTEM_TIMEOUT_ENTRIES],
)
async def test_subsystem_timeout_entry_env_override(
    namespace: str,
    key: str,
    _expected_type: SettingType,
    _expected_default: str,
    env_value: str,
    _expected_read_only_post_init: bool,
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_var = f"SYNTHORG_{namespace.upper()}_{key.upper()}"
    monkeypatch.setenv(env_var, env_value)
    result = await service.get(namespace, key)
    assert result.value == env_value
    assert result.source is SettingSource.ENVIRONMENT
