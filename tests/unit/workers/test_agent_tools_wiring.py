"""Tests for the boot agent-tool wiring from ``tools`` settings.

Pins the four ghost-wired ``build_*_tools_runtime_or_none`` builders: each
returns a runtime bundle only when its feature flag is on AND its bound
surface is non-empty (a connection for forge / chat, a target allowlist for
deploy / publish), and fails open to ``None`` on a missing catalog or a
settings-resolution failure (never crashing boot).
"""

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.persistence.integration_inmemory import InMemoryConnectionRepository
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.deploy._runtime import DeployToolsRuntime
from synthorg.tools.forge._runtime import ForgeToolsRuntime
from synthorg.tools.publish._runtime import PublishToolsRuntime
from synthorg.workers._agent_tools_wiring import (
    build_chat_tools_runtime_or_none,
    build_connection_tool_runtimes,
    build_deploy_tools_runtime_or_none,
    build_forge_tools_runtime_or_none,
    build_publish_tools_runtime_or_none,
)
from tests._shared import FakeClock, mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


class _StubSecretBackend:
    """Minimal ``SecretBackend`` so a real catalog constructs."""

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("stub")

    async def store(self, secret_id: NotBlankStr, value: bytes) -> None:
        del secret_id, value

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        del secret_id
        return None

    async def delete(self, secret_id: NotBlankStr) -> bool:
        del secret_id
        return False

    async def rotate(self, old_id: NotBlankStr, new_value: bytes) -> NotBlankStr:
        del new_value
        return old_id

    async def close(self) -> None:
        return None


def _real_catalog() -> ConnectionCatalog:
    return ConnectionCatalog(InMemoryConnectionRepository(), _StubSecretBackend())


class _StubCatalog:
    """Stand-in connection catalog; only its presence matters for wiring."""


def _app_state(*, catalog: object, workspace_root: Path | None = None) -> AppState:
    # One namespace answers every slice: the wiring reads the connection
    # catalog and the workspace root, and no builder distinguishes which
    # slice class carried which field.
    slice_fields = SimpleNamespace(
        connection_catalog=catalog,
        agent_workspace_root=workspace_root,
    )
    return cast(
        "AppState",
        SimpleNamespace(
            slice=lambda _cls: slice_fields,
            clock=FakeClock(),
        ),
    )


def _resolver(
    *,
    enabled: bool,
    connection: str = "conn",
    timeout: float = 30.0,
    max_read_chars: int = 100_000,
) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = enabled
    resolver.get_str.return_value = connection
    resolver.get_float.return_value = timeout
    resolver.get_int.return_value = max_read_chars
    return cast("ConfigResolver", resolver)


def _patch(monkeypatch: pytest.MonkeyPatch, resolver: ConfigResolver) -> None:
    monkeypatch.setattr(
        "synthorg.workers._agent_tools_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )


class TestForgeToolsWiring:
    async def test_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=False))
        result = await build_forge_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_returns_none_when_no_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True))
        result = await build_forge_tools_runtime_or_none(_app_state(catalog=None))
        assert result is None

    async def test_returns_none_when_no_connection_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True, connection=""))
        result = await build_forge_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_builds_runtime_on_happy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True))
        result = await build_forge_tools_runtime_or_none(
            _app_state(catalog=_real_catalog())
        )
        assert isinstance(result, ForgeToolsRuntime)
        assert result.connection_name == "conn"
        assert result.max_read_chars == 100_000

    async def test_returns_none_when_enabled_resolve_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        result = await build_forge_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_returns_none_when_settings_resolve_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.return_value = True
        resolver.get_str.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        result = await build_forge_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None


class TestChatToolsWiring:
    async def test_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=False))
        result = await build_chat_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_returns_none_when_no_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True))
        result = await build_chat_tools_runtime_or_none(_app_state(catalog=None))
        assert result is None

    async def test_returns_none_when_no_connection_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True, connection=""))
        result = await build_chat_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_builds_runtime_on_happy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=True))
        result = await build_chat_tools_runtime_or_none(
            _app_state(catalog=_real_catalog())
        )
        assert isinstance(result, ChatToolsRuntime)
        assert result.connection_name == "conn"

    async def test_returns_none_when_settings_resolve_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.return_value = True
        resolver.get_str.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        result = await build_chat_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None


def _target_resolver(
    *,
    enabled: bool,
    targets: str = "staging, production",
    timeout: float = 300.0,
    ints: dict[str, int] | None = None,
) -> ConfigResolver:
    """Build a resolver for a target-allowlisted family.

    Returns:
        A resolver answering the family's flag, its target allowlist, and
        each of its integer budgets by key, so a builder reading two
        budgets cannot pass by reading one of them twice.
    """
    by_key = ints or {}
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = enabled
    resolver.get_str.return_value = targets
    resolver.get_float.return_value = timeout
    resolver.get_int.side_effect = lambda _ns, key: by_key[key]
    return cast("ConfigResolver", resolver)


class TestDeployToolsWiring:
    async def test_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, _target_resolver(enabled=False))
        result = await build_deploy_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_returns_none_when_no_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            _target_resolver(enabled=True, ints={"deploy_tools_max_log_chars": 50_000}),
        )
        result = await build_deploy_tools_runtime_or_none(_app_state(catalog=None))
        assert result is None

    async def test_returns_none_when_allowlist_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            _target_resolver(
                enabled=True,
                targets="  ,  ",
                ints={"deploy_tools_max_log_chars": 50_000},
            ),
        )
        result = await build_deploy_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None

    async def test_builds_runtime_on_happy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            _target_resolver(enabled=True, ints={"deploy_tools_max_log_chars": 50_000}),
        )
        result = await build_deploy_tools_runtime_or_none(
            _app_state(catalog=_real_catalog())
        )
        assert isinstance(result, DeployToolsRuntime)
        assert result.allowed_targets == frozenset({"staging", "production"})
        assert result.timeout_seconds == 300.0
        assert result.max_log_chars == 50_000

    async def test_returns_none_when_settings_resolve_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.return_value = True
        resolver.get_str.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        result = await build_deploy_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog())
        )
        assert result is None


_PUBLISH_INTS = {
    "publish_tools_max_manifest_bytes": 1_048_576,
    "publish_tools_max_image_bytes": 2_147_483_648,
}


class TestPublishToolsWiring:
    async def test_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch(monkeypatch, _target_resolver(enabled=False))
        result = await build_publish_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog(), workspace_root=tmp_path)
        )
        assert result is None

    async def test_returns_none_when_no_catalog(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch(monkeypatch, _target_resolver(enabled=True, ints=_PUBLISH_INTS))
        result = await build_publish_tools_runtime_or_none(
            _app_state(catalog=None, workspace_root=tmp_path)
        )
        assert result is None

    async def test_returns_none_when_allowlist_is_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch(
            monkeypatch,
            _target_resolver(enabled=True, targets="", ints=_PUBLISH_INTS),
        )
        result = await build_publish_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog(), workspace_root=tmp_path)
        )
        assert result is None

    async def test_builds_runtime_on_happy_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch(monkeypatch, _target_resolver(enabled=True, ints=_PUBLISH_INTS))
        result = await build_publish_tools_runtime_or_none(
            _app_state(catalog=_real_catalog(), workspace_root=tmp_path)
        )
        assert isinstance(result, PublishToolsRuntime)
        assert result.allowed_targets == frozenset({"staging", "production"})
        assert result.max_manifest_bytes == 1_048_576
        assert result.max_image_bytes == 2_147_483_648
        assert result.workspace_root == tmp_path

    async def test_returns_none_when_settings_resolve_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.return_value = True
        resolver.get_str.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        result = await build_publish_tools_runtime_or_none(
            _app_state(catalog=_StubCatalog(), workspace_root=tmp_path)
        )
        assert result is None


class TestConnectionToolRuntimesBundle:
    """The bundle is the single owner of which families a runtime carries."""

    async def test_every_family_off_yields_an_empty_bundle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch(monkeypatch, _target_resolver(enabled=False))
        bundle = await build_connection_tool_runtimes(
            _app_state(catalog=_StubCatalog(), workspace_root=tmp_path)
        )
        assert bundle == ConnectionToolRuntimes()

    async def test_every_family_is_resolved_into_its_own_field(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # One resolver answers every family, so a builder wired into the
        # wrong field would surface as a type mismatch on its own row.
        _patch(
            monkeypatch,
            _target_resolver(
                enabled=True,
                targets="staging",
                ints={
                    "forge_tools_max_read_chars": 100_000,
                    "deploy_tools_max_log_chars": 50_000,
                    **_PUBLISH_INTS,
                },
            ),
        )
        bundle = await build_connection_tool_runtimes(
            _app_state(catalog=_real_catalog(), workspace_root=tmp_path)
        )
        assert isinstance(bundle.forge, ForgeToolsRuntime)
        assert isinstance(bundle.chat, ChatToolsRuntime)
        assert isinstance(bundle.deploy, DeployToolsRuntime)
        assert isinstance(bundle.publish, PublishToolsRuntime)
