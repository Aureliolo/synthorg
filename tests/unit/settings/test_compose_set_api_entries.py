"""Coverage for the compose-set API entries.

These settings describe the socket uvicorn already opened and the middleware
Litestar already mounted, so the process cannot change them about itself; the
deployment supplies them and the registry entry exists for ``/settings``
discoverability only. ``compose_set=True`` makes ``SettingsService.set()``
reject mutation, and the read path collapses the chain to env > default (the
DB row is never consulted).
"""

import os

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.errors import SettingReadOnlyError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class _RepoMustNotBeReadError(RuntimeError):
    """Sentinel raised by the test repo if compose_set misroutes a get.

    A compose-set entry must NEVER consult the persistence layer at read
    time (the env > default short-circuit applies).  The fixture wires this
    exception into ``repo.get`` so any future regression that accidentally
    hits the repository for a compose-set key surfaces immediately as a test
    failure rather than a silent default fallback.
    """


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> SettingsService:
    # Strip every SYNTHORG_API_* override so the registry resolution
    # falls through to the documented default.  Without this, an
    # operator-set env var on the developer machine would mask the
    # default-resolution assertion.
    for env_key in tuple(os.environ):
        if env_key.startswith("SYNTHORG_API_"):
            monkeypatch.delenv(env_key, raising=False)

    repo = mock_of[SettingsRepository]()
    repo.get.side_effect = _RepoMustNotBeReadError(
        "compose_set keys must not consult the persistence layer"
    )
    repo.get_namespace.return_value = ()
    repo.list_items.return_value = ()
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


# (namespace, key, expected_default).  ``RootConfig`` reads these once at
# startup and the resulting values are baked into uvicorn / Litestar /
# middleware at app construction.  The matrix locks ``compose_set=True`` on
# each entry so ``SettingsService.set()`` rejects a write rather than storing
# a value the running process will never read.
_COMPOSE_SET_API_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("api", "api_prefix", "/api/v1"),
    ("api", "server_host", "127.0.0.1"),
    ("api", "server_port", "3001"),
    ("api", "cors_allowed_origins", "[]"),
    ("api", "trusted_proxies", "[]"),
    # Litestar applies rate-limit exclusions when the middleware is mounted,
    # never per request, so moving them means rebuilding the middleware stack.
    ("api", "rate_limit_exclude_paths", '["/api/v1/healthz", "/api/v1/readyz"]'),
    # TLS paths: uvicorn bakes resolved file paths into the server at
    # construction; runtime ``set()`` cannot retroactively swap the
    # cert on the listening socket.
    ("api", "ssl_certfile", ""),
    ("api", "ssl_keyfile", ""),
    ("api", "ssl_ca_certs", ""),
)


@pytest.mark.parametrize(
    ("namespace", "key", "expected_default"),
    _COMPOSE_SET_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _COMPOSE_SET_API_ENTRIES],
)
def test_compose_set_entry_carries_compose_set(
    namespace: str,
    key: str,
    expected_default: str,
) -> None:
    """The registry entry must advertise itself as compose-set."""
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"setting {namespace}.{key} missing from registry"
    assert defn.compose_set is True, f"{namespace}.{key} must be compose_set=True"
    assert defn.default == expected_default


@pytest.mark.parametrize(
    ("namespace", "key", "_expected_default"),
    _COMPOSE_SET_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _COMPOSE_SET_API_ENTRIES],
)
async def test_compose_set_entry_set_rejects(
    namespace: str,
    key: str,
    _expected_default: str,
    service: SettingsService,
) -> None:
    """``service.set()`` must raise ``SettingReadOnlyError`` on these."""
    with pytest.raises(SettingReadOnlyError):
        await service.set(namespace, key, "ignored")


@pytest.mark.parametrize(
    ("namespace", "key", "expected_default"),
    _COMPOSE_SET_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _COMPOSE_SET_API_ENTRIES],
)
async def test_compose_set_entry_default_resolves(
    namespace: str,
    key: str,
    expected_default: str,
    service: SettingsService,
) -> None:
    """With no env override, the entry resolves to its documented default."""
    result = await service.get(namespace, key)
    assert result.value == expected_default
