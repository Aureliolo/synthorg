"""Coverage for the URL- and port-shaped settings registered for
operator override of third-party endpoints.

Each entry covers: registry presence + documented default + type +
default resolution + env override.
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
    model_config = ConfigDict(frozen=True)


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get.return_value = None
    repo.get_namespace.return_value = ()
    repo.get_all.return_value = ()
    return SettingsService(
        repository=repo,
        registry=get_registry(),
        config=_FakeConfig(),
    )


# (namespace, key, type, default, env_value).
_URL_PORT_ENTRIES: tuple[tuple[str, str, SettingType, str, str], ...] = (
    (
        "notifications",
        "ntfy_default_url",
        SettingType.STRING,
        "https://ntfy.sh",
        "https://ntfy.example.com",
    ),
    (
        "observability",
        "tsa_endpoint_freetsa",
        SettingType.STRING,
        "https://freetsa.org/tsr",
        "https://tsa.example.com/tsr",
    ),
    (
        "observability",
        "tsa_endpoint_digicert",
        SettingType.STRING,
        "https://timestamp.digicert.com",
        "https://timestamp.digicert.example.com",
    ),
    (
        "observability",
        "tsa_endpoint_sectigo",
        SettingType.STRING,
        "https://timestamp.sectigo.com",
        "https://timestamp.sectigo.example.com",
    ),
    (
        "integrations",
        "github_api_url",
        SettingType.STRING,
        "https://api.github.com",
        "https://github.example.com/api/v3",
    ),
    (
        "providers",
        "ollama_default_port",
        SettingType.INTEGER,
        "11434",
        "11500",
    ),
)


@pytest.mark.parametrize(
    ("namespace", "key", "expected_type", "expected_default", "_env_value"),
    _URL_PORT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _URL_PORT_ENTRIES],
)
def test_url_port_entry_registered(
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


@pytest.mark.parametrize(
    ("namespace", "key", "_expected_type", "expected_default", "_env_value"),
    _URL_PORT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _URL_PORT_ENTRIES],
)
async def test_url_port_entry_default_resolves(
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
    _URL_PORT_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, *_ in _URL_PORT_ENTRIES],
)
async def test_url_port_entry_env_override(
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


def test_github_api_url_pattern_documented() -> None:
    """The URL pattern guards write-time validation (env reads pass through)."""
    defn = get_registry().get("integrations", "github_api_url")
    assert defn is not None
    assert defn.validator_pattern is not None
    # Documented enterprise example must satisfy the pattern, ".invalid" must not.
    import re

    pattern = re.compile(defn.validator_pattern)
    assert pattern.match("https://github.example.com/api/v3")
    assert not pattern.match("not-a-url")
