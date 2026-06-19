"""Tests for ProviderHealthProber."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from synthorg.config.schema import ProviderConfig
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderHealthTracker,
)
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.providers.health_prober_helpers import (
    build_auth_headers as _build_auth_headers,
)
from synthorg.providers.health_prober_helpers import (
    build_ping_url as _build_ping_url,
)
from synthorg.settings import (
    definitions as _settings_definitions,  # noqa: F401 -- side-effect import populates the registry
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_int
from synthorg.settings.resolver import ConfigResolver


def _make_local_config(
    *,
    base_url: str = "http://localhost:11434",
    litellm_provider: str | None = "ollama",
    auth_type: str = "none",
    api_key: str | None = None,
    connection_name: str | None = None,
) -> MagicMock:
    """Build a mock ProviderConfig for a local provider."""
    mock = MagicMock(spec=ProviderConfig)
    mock.base_url = base_url
    mock.litellm_provider = litellm_provider
    mock.auth_type = auth_type
    mock.api_key = api_key
    # The prober resolves credentials from the catalog via connection_name;
    # None makes the probe skip auth-header resolution (no catalog wired here).
    mock.connection_name = connection_name
    return mock


def _make_prober(
    tracker: ProviderHealthTracker | None = None,
    configs: dict[str, MagicMock] | None = None,
    *,
    discovery_policy_loader: AsyncMock | None = None,
    interval_seconds: int = 3600,
) -> tuple[ProviderHealthProber, ProviderHealthTracker]:
    """Build a prober with a mock config_resolver.

    Returns:
        Tuple of (prober, tracker) for assertion convenience.
    """
    trk = tracker or ProviderHealthTracker()
    config_resolver = MagicMock(spec=ConfigResolver)
    config_resolver.get_provider_configs = AsyncMock(
        spec=ConfigResolver.get_provider_configs,
        return_value=configs or {"test-local": _make_local_config()},
    )
    config_resolver.get_int = AsyncMock(
        spec=ConfigResolver.get_int,
        return_value=registered_default_int(
            SettingNamespace.PROVIDERS.value, "ollama_default_port"
        ),
    )
    prober = ProviderHealthProber(
        trk,
        config_resolver,
        discovery_policy_loader=discovery_policy_loader,
        interval_seconds=interval_seconds,
    )
    return prober, trk


def _patch_httpx(
    *,
    status_code: int | None = None,
    side_effect: Exception | None = None,
) -> _PatchCtx:
    """Context manager that patches httpx.AsyncClient for probe tests."""
    return _PatchCtx(status_code=status_code, side_effect=side_effect)


class _PatchCtx:
    def __init__(
        self,
        *,
        status_code: int | None = None,
        side_effect: Exception | None = None,
    ) -> None:
        self._status_code = status_code
        self._side_effect = side_effect
        self._patcher = patch(
            "synthorg.providers.health_prober.httpx.AsyncClient",
        )
        self.mock_client_cls: MagicMock | None = None

    def __enter__(self) -> _PatchCtx:
        # Capture the real classes BEFORE entering the patch context.
        # ``patch("synthorg.providers.health_prober.httpx.AsyncClient")``
        # mutates the shared httpx module's attribute, so reading
        # ``httpx.AsyncClient`` after ``__enter__`` returns a MagicMock
        # (which mock rejects as a spec target). The ``spec=`` calls
        # below need the real classes so AsyncMock auto-generates real
        # method children.
        real_async_client = httpx.AsyncClient
        real_response = httpx.Response
        self.mock_client_cls = self._patcher.__enter__()
        mock_client = AsyncMock(spec=real_async_client)
        if self._side_effect is not None:
            mock_client.get.side_effect = self._side_effect
        else:
            mock_response = MagicMock(spec=real_response)
            mock_response.status_code = self._status_code or 200
            mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        self.mock_client_cls.return_value = mock_client
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._patcher.__exit__(exc_type, exc_val, exc_tb)  # type: ignore[arg-type]


@pytest.mark.unit
class TestBuildPingUrl:
    # Read the canonical default from the settings registry rather
    # than hardcoding 11434: the registry is the single source of
    # truth, and a literal here would re-create a parallel-default
    # drift. If ``providers.ollama_default_port`` moves, these tests
    # follow automatically.
    OLLAMA_PORT: int = registered_default_int(
        SettingNamespace.PROVIDERS.value, "ollama_default_port"
    )

    def test_root_url_provider_returns_root(self) -> None:
        # Provider type "ollama" uses root URL (liveness string)
        assert (
            _build_ping_url(
                "http://localhost:11434", "ollama", ollama_port=self.OLLAMA_PORT
            )
            == "http://localhost:11434"
        )

    def test_local_detected_by_port(self) -> None:
        assert (
            _build_ping_url(
                f"http://host:{self.OLLAMA_PORT}/",
                None,
                ollama_port=self.OLLAMA_PORT,
            )
            == f"http://host:{self.OLLAMA_PORT}"
        )

    def test_standard_appends_models(self) -> None:
        assert (
            _build_ping_url(
                "http://localhost:1234/v1", None, ollama_port=self.OLLAMA_PORT
            )
            == "http://localhost:1234/v1/models"
        )

    def test_strips_trailing_slash(self) -> None:
        assert (
            _build_ping_url(
                "http://localhost:8000/v1/", "test-api", ollama_port=self.OLLAMA_PORT
            )
            == "http://localhost:8000/v1/models"
        )

    def test_port_in_path_does_not_match(self) -> None:
        """Port heuristic uses urlparse: a port-shaped path segment must not match."""
        path_url = f"http://host:8080/api/{self.OLLAMA_PORT}/v1"
        result = _build_ping_url(path_url, None, ollama_port=self.OLLAMA_PORT)
        assert result == f"{path_url}/models"


@pytest.mark.unit
class TestBuildAuthHeaders:
    @pytest.mark.parametrize(
        ("auth_type", "api_key", "expected"),
        [
            ("api_key", "sk-123", {"Authorization": "Bearer sk-123"}),
            ("subscription", "sub-tok", {"Authorization": "Bearer sub-tok"}),
            ("api_key", None, {}),
            ("api_key", "", {}),
            ("none", "ignored", {}),
            ("oauth", "token", {}),
            ("custom_header", "val", {}),
        ],
        ids=[
            "api_key_with_key",
            "subscription_with_key",
            "api_key_none",
            "api_key_empty",
            "none_type",
            "oauth_type",
            "custom_header_type",
        ],
    )
    def test_header_construction(
        self,
        auth_type: str,
        api_key: str | None,
        expected: dict[str, str],
    ) -> None:
        assert _build_auth_headers(auth_type, api_key) == expected


@pytest.mark.unit
class TestProviderHealthProber:
    async def test_probe_records_success(self) -> None:
        prober, tracker = _make_prober()
        with _patch_httpx(status_code=200):
            await prober._probe_all()

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.UP
        assert summary.calls_last_24h == 1

    async def test_probe_records_failure(self) -> None:
        prober, tracker = _make_prober()
        with _patch_httpx(side_effect=httpx.ConnectError("refused")):
            await prober._probe_all()

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.DOWN
        assert summary.calls_last_24h == 1

    async def test_probe_records_server_error(self) -> None:
        """HTTP 5xx responses are recorded as failures."""
        prober, tracker = _make_prober()
        with _patch_httpx(status_code=503):
            await prober._probe_all()

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.DOWN

    async def test_probe_records_timeout(self) -> None:
        """Timeout exceptions are recorded as failures."""
        prober, tracker = _make_prober()
        with _patch_httpx(side_effect=httpx.ReadTimeout("probe timeout")):
            await prober._probe_all()

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.DOWN

    async def test_skips_cloud_providers(self) -> None:
        mock_config = MagicMock(spec=ProviderConfig)
        mock_config.base_url = None  # cloud provider

        prober, _ = _make_prober(configs={"test-cloud": mock_config})

        with _patch_httpx() as ctx:
            await prober._probe_all()
            assert ctx.mock_client_cls is not None
            ctx.mock_client_cls.assert_not_called()

    async def test_skips_recently_active_providers(self) -> None:
        tracker = ProviderHealthTracker()
        await tracker.record(
            ProviderHealthRecord(
                provider_name="test-local",
                timestamp=datetime.now(UTC),
                success=True,
                response_time_ms=50.0,
            ),
        )

        prober, _ = _make_prober(tracker=tracker)

        with _patch_httpx() as ctx:
            await prober._probe_all()
            assert ctx.mock_client_cls is not None
            ctx.mock_client_cls.assert_not_called()

    async def test_ssrf_blocked_provider_skipped(self) -> None:
        """SSRF-blocked providers are skipped without recording failure."""
        from synthorg.providers.discovery_policy import ProviderDiscoveryPolicy

        # Only "allowed.com:8080" in allowlist -- "blocked.internal:8080"
        # will be rejected by the SSRF check.
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("allowed.com:8080",),
        )

        # ``spec=`` against an async-callable signature so the gate's
        # mock-spec ConcreteClass rule is satisfied; the body is never
        # invoked because ``return_value=`` short-circuits the call.
        async def _policy_loader_spec() -> ProviderDiscoveryPolicy:
            raise NotImplementedError

        policy_loader = AsyncMock(spec=_policy_loader_spec, return_value=policy)

        configs = {
            "test-blocked": _make_local_config(
                base_url="http://blocked.internal:8080",
                litellm_provider=None,
            ),
        }
        prober, tracker = _make_prober(
            configs=configs,
            discovery_policy_loader=policy_loader,
        )

        with _patch_httpx() as ctx:
            await prober._probe_all()
            assert ctx.mock_client_cls is not None
            ctx.mock_client_cls.assert_not_called()

        # SSRF-blocked provider should remain UNKNOWN (zero records)
        summary = await tracker.get_summary("test-blocked")
        assert summary.health_status == ProviderHealthStatus.UNKNOWN
        assert summary.calls_last_24h == 0

    @pytest.mark.parametrize("invalid_interval", [0, -5])
    def test_invalid_interval_raises(self, invalid_interval: int) -> None:
        """interval_seconds < 1 raises ValueError."""
        tracker = ProviderHealthTracker()
        config_resolver = MagicMock(spec=ConfigResolver)
        with pytest.raises(ValueError, match=r"interval_seconds must be >= 1"):
            ProviderHealthProber(
                tracker,
                config_resolver,
                interval_seconds=invalid_interval,
            )


@pytest.mark.unit
class TestProberLifecycle:
    """Tests for start/stop lifecycle management."""

    async def test_start_creates_background_task(self) -> None:
        prober, _ = _make_prober()
        # Before start: no task
        pre_task = prober._task
        assert pre_task is None
        await prober.start()
        # After start: task is running
        post_task = prober._task
        assert post_task is not None
        assert not post_task.done()
        await prober.stop()

    async def test_stop_cancels_task(self) -> None:
        prober, _ = _make_prober()
        await prober.start()
        task = prober._task
        assert task is not None
        await prober.stop()
        assert prober._task is None
        assert task.done()

    async def test_double_start_is_idempotent(self) -> None:
        prober, _ = _make_prober()
        await prober.start()
        first_task = prober._task
        await prober.start()
        assert prober._task is first_task
        await prober.stop()

    async def test_stop_before_start_is_safe(self) -> None:
        prober, _ = _make_prober()
        await prober.stop()  # Should not raise

    async def test_start_after_stop_restarts(self) -> None:
        prober, _ = _make_prober()
        await prober.start()
        await prober.stop()
        # Restart: new task created
        await prober.start()
        restarted_task = prober._task
        assert restarted_task is not None
        await prober.stop()

    async def test_run_loop_continues_on_probe_error(self) -> None:
        """The loop catches exceptions from _probe_all and continues."""
        call_count = 0
        done_event = asyncio.Event()
        tracker = ProviderHealthTracker()
        config_resolver = MagicMock(spec=ConfigResolver)
        config_resolver.get_provider_configs = AsyncMock(
            spec=ConfigResolver.get_provider_configs,
            return_value={},
        )
        config_resolver.get_bool = AsyncMock(
            spec=ConfigResolver.get_bool,
            return_value=True,
        )

        prober = ProviderHealthProber(
            tracker,
            config_resolver,
            interval_seconds=1,
        )

        async def _counting_get() -> dict[str, MagicMock]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "test error"
                raise RuntimeError(msg)
            # Second call: signal completion and stop the loop
            done_event.set()
            prober._stop_event.set()
            return {}

        config_resolver.get_provider_configs = AsyncMock(
            spec=ConfigResolver.get_provider_configs,
            side_effect=_counting_get,
        )

        # Patch the interval so wait_for(stop_event.wait(), timeout=0)
        # times out instantly between probe cycles instead of waiting 1s.
        with patch.object(prober, "_interval", 0):
            await prober.start()
            # Wait for the second call deterministically (no timing)
            await asyncio.wait_for(done_event.wait(), timeout=10)
            await prober.stop()

        assert call_count >= 2  # First call failed, loop continued
