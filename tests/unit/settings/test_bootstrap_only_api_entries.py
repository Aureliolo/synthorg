"""Coverage for the bootstrap-only API entries.

These settings are read at process start via ``RootConfig`` and held
for the lifetime of the running process; the registry entry exists for
``/settings`` discoverability only.  ``read_only_post_init=True`` makes
``SettingsService.set()`` reject mutation, and the resolver's
read-only-post-init branch collapses the chain to env > YAML > default
(the DB row is never consulted).
"""

import os
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.errors import SettingReadOnlyError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)


class _RepoMustNotBeReadError(RuntimeError):
    """Sentinel raised by the test repo if read_only_post_init misroutes a get.

    A bootstrap-only entry must NEVER consult the persistence layer at
    read time (env > YAML > default short-circuit applies).  The fixture
    wires this exception into ``repo.get`` so any future regression that
    accidentally hits the repository for a read-only-post-init key
    surfaces immediately as a test failure rather than a silent default
    fallback.
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

    repo = AsyncMock(spec=SettingsRepository)
    repo.get.side_effect = _RepoMustNotBeReadError(
        "read_only_post_init keys must not consult the persistence layer"
    )
    repo.get_namespace.return_value = ()
    repo.get_all.return_value = ()
    return SettingsService(
        repository=repo,
        registry=get_registry(),
        config=_FakeConfig(),
    )


# (namespace, key, expected_default).  These five API entries are
# bootstrap-only: ``RootConfig`` reads them once at startup and the
# resulting values are baked into uvicorn / Litestar / middleware at
# app construction.  The matrix locks ``read_only_post_init=True`` on
# each entry so ``SettingsService.set()`` rejects mutations that would
# otherwise appear to take effect but never actually flow through to
# the running process until a restart.
_BOOTSTRAP_ONLY_API_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("api", "api_prefix", "/api/v1"),
    ("api", "server_host", "127.0.0.1"),
    ("api", "server_port", "3001"),
    ("api", "cors_allowed_origins", "[]"),
    ("api", "trusted_proxies", "[]"),
    # TLS paths: uvicorn bakes resolved file paths into the server at
    # construction; runtime ``set()`` cannot retroactively swap the
    # cert on the listening socket.
    ("api", "ssl_certfile", ""),
    ("api", "ssl_keyfile", ""),
    ("api", "ssl_ca_certs", ""),
)


@pytest.mark.parametrize(
    ("namespace", "key", "expected_default"),
    _BOOTSTRAP_ONLY_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _BOOTSTRAP_ONLY_API_ENTRIES],
)
def test_bootstrap_only_entry_carries_read_only_post_init(
    namespace: str,
    key: str,
    expected_default: str,
) -> None:
    """The registry entry must advertise itself as read-only-post-init."""
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"setting {namespace}.{key} missing from registry"
    assert defn.read_only_post_init is True, (
        f"{namespace}.{key} must be read_only_post_init=True"
    )
    assert defn.restart_required is True, (
        f"{namespace}.{key} must be restart_required=True (implied by"
        " read_only_post_init)"
    )
    assert defn.default == expected_default


@pytest.mark.parametrize(
    ("namespace", "key", "_expected_default"),
    _BOOTSTRAP_ONLY_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _BOOTSTRAP_ONLY_API_ENTRIES],
)
async def test_bootstrap_only_entry_set_rejects(
    namespace: str,
    key: str,
    _expected_default: str,
    service: SettingsService,
) -> None:
    """``service.set()`` must raise ``SettingReadOnlyError`` on bootstrap entries."""
    with pytest.raises(SettingReadOnlyError):
        await service.set(namespace, key, "ignored")


@pytest.mark.parametrize(
    ("namespace", "key", "expected_default"),
    _BOOTSTRAP_ONLY_API_ENTRIES,
    ids=[f"{ns}.{k}" for ns, k, _ in _BOOTSTRAP_ONLY_API_ENTRIES],
)
async def test_bootstrap_only_entry_default_resolves(
    namespace: str,
    key: str,
    expected_default: str,
    service: SettingsService,
) -> None:
    """With no env/YAML override, the entry resolves to its documented default."""
    result = await service.get(namespace, key)
    assert result.value == expected_default
