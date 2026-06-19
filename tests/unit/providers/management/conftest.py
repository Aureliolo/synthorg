"""Shared fixtures for provider management tests."""

from collections.abc import AsyncIterator

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.dto_providers import CreateProviderRequest
from synthorg.api.state import AppState
from synthorg.config.schema import ProviderModelConfig, RootConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.management.service import ProviderManagementService
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of
from tests._shared import make_app_state
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend


@pytest.fixture
async def fake_persistence() -> AsyncIterator[FakePersistenceBackend]:
    """In-memory persistence backend for provider management tests."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def fake_message_bus() -> AsyncIterator[FakeMessageBus]:
    """In-memory message bus for provider management tests."""
    bus = FakeMessageBus()
    await bus.start()
    yield bus
    await bus.stop()


@pytest.fixture
def root_config() -> RootConfig:
    """Default RootConfig for provider management tests."""
    return RootConfig(company_name="test-company")


@pytest.fixture
def encryptor() -> SettingsEncryptor:
    """SettingsEncryptor with a freshly generated Fernet key."""
    from cryptography.fernet import Fernet

    return SettingsEncryptor(Fernet.generate_key())


@pytest.fixture
def settings_service(
    fake_persistence: FakePersistenceBackend,
    root_config: RootConfig,
    encryptor: SettingsEncryptor,
) -> SettingsService:
    """SettingsService wired to fake persistence and a fresh registry."""
    return SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
        encryptor=encryptor,
    )


class _InMemorySecretBackend:
    """Functional dict-backed secret backend for provider-management tests.

    Lets the wired ConnectionCatalog round-trip mint (store) and resolve
    (retrieve) so the catalog-only credential path is exercised end-to-end
    without a real encrypted backend.
    """

    def __init__(self) -> None:
        self._secrets: dict[str, bytes] = {}
        self._counter = 0

    @property
    def backend_name(self) -> str:
        return "in-memory-test"

    async def store(self, secret_id: str, value: bytes) -> None:
        self._secrets[secret_id] = value

    async def retrieve(self, secret_id: str) -> bytes | None:
        return self._secrets.get(secret_id)

    async def delete(self, secret_id: str) -> bool:
        return self._secrets.pop(secret_id, None) is not None

    async def rotate(self, old_id: str, new_value: bytes) -> str:
        self._counter += 1
        new_id = f"{old_id}-rot{self._counter}"
        self._secrets[new_id] = new_value
        self._secrets.pop(old_id, None)
        return new_id

    async def close(self) -> None:
        return None


@pytest.fixture
def app_state(
    root_config: RootConfig,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    settings_service: SettingsService,
) -> AppState:
    """AppState assembled from fakes for isolated service tests.

    Wires a functional in-memory ConnectionCatalog onto
    ``provider_credential_catalog`` so the catalog-only credential path
    (mint-on-create, resolve-at-probe) works under unit tests.
    """
    from synthorg.api.approval_store import ApprovalStore
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.state import IntegrationsStateSlice
    from synthorg.persistence.integration_stubs import InMemoryConnectionRepository
    from synthorg.settings.resolver import ConfigResolver

    state = make_app_state(
        config=root_config,
        approval_store=ApprovalStore(),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        settings_service=settings_service,
        config_resolver=ConfigResolver(
            settings_service=settings_service,
            config=root_config,
        ),
    )
    catalog = ConnectionCatalog(
        repository=InMemoryConnectionRepository(),
        secret_backend=_InMemorySecretBackend(),  # type: ignore[arg-type]
    )
    state.wire(IntegrationsStateSlice, provider_credential_catalog=catalog)
    return state


@pytest.fixture
def service(
    settings_service: SettingsService,
    app_state: AppState,
    root_config: RootConfig,
) -> ProviderManagementService:
    """ProviderManagementService wired to fake-backed app state."""
    return ProviderManagementService(
        settings_service=settings_service,
        config_resolver=config_resolver_of(app_state),
        app_state=app_state,
        config=root_config,
    )


def make_create_request(
    name: str = "test-provider",
    auth_type: AuthType = AuthType.NONE,
    models: tuple[ProviderModelConfig, ...] | None = None,
    **kwargs: object,
) -> CreateProviderRequest:
    """Build a ``CreateProviderRequest`` with sensible defaults."""
    if models is None:
        models = (
            ProviderModelConfig(
                id="test-model-001",
                alias="medium",
            ),
        )
    return CreateProviderRequest(
        name=name,
        driver="litellm",
        auth_type=auth_type,
        models=models,
        **kwargs,  # type: ignore[arg-type]
    )
