"""Tests for ProviderHealthProber."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from structlog.testing import capture_logs

from synthorg.config.schema import ProviderConfig
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_PROBE_SKIPPED as PROBE_SKIPPED,
)
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    resolve_discovery_target,
)
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
from synthorg.providers.health_prober_targets import (
    ProbeTarget,
    _base_url_is_required,
    resolve_probe_target,
)
from synthorg.settings import (
    definitions as _settings_definitions,  # noqa: F401 -- side-effect import populates the registry
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_int
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.network_validator import DnsValidationOk

_LOCAL_CONFIG_FIELDS: Mapping[str, object] = {
    "base_url": "http://localhost:11434",
    "litellm_provider": "ollama",
    "auth_type": "none",
    "api_key": None,
    # The missing-base-url gate reads preset_name to tell a cloud provider
    # from a self-hosted one; the probe resolves credentials from the catalog
    # via connection_name, and None skips auth-header resolution (no catalog
    # is wired here).
    "preset_name": None,
    "connection_name": None,
}


def _make_local_config(**overrides: object) -> MagicMock:
    """Build a mock ProviderConfig for a local provider.

    Every field is assigned explicitly because ``spec=`` mirrors class
    attributes only, and plain Pydantic fields are not among them.

    Args:
        **overrides: Field values replacing the local-provider defaults.

    Returns:
        The configured mock.
    """
    mock = MagicMock(spec=ProviderConfig)
    for field, value in {**_LOCAL_CONFIG_FIELDS, **overrides}.items():
        setattr(mock, field, value)
    return mock


def _make_prober(
    tracker: ProviderHealthTracker | None = None,
    configs: dict[str, MagicMock] | None = None,
    *,
    discovery_policy_loader: AsyncMock | None = None,
    interval_seconds: int = 3600,
    enabled: bool = True,
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
    # ``api.health_prober_enabled``: resolved live per cycle and by the
    # on-demand probe, so it must answer on the mock rather than falling
    # through to the resolver-failure fail-safe.
    config_resolver.get_bool = AsyncMock(
        spec=ConfigResolver.get_bool,
        return_value=enabled,
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
        # The module-local name, NOT ``...._probe_request.httpx.AsyncClient``:
        # the latter resolves through the shared httpx module and would swap
        # the class out process-wide, so any library that happens to build a
        # client inside this window (litellm does, on its lazy first use)
        # would both receive the mock and register as a call on it.
        self._patcher = patch(
            "synthorg.providers._probe_request.AsyncClient",
        )
        self.mock_client_cls: MagicMock | None = None

    def __enter__(self) -> _PatchCtx:
        self.mock_client_cls = self._patcher.__enter__()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        if self._side_effect is not None:
            mock_client.get.side_effect = self._side_effect
        else:
            mock_response = MagicMock(spec=httpx.Response)
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

    async def test_probe_records_rate_limited_as_failure(self) -> None:
        """HTTP 429 is a 4xx but a rate-limited endpoint is NOT healthy."""
        prober, tracker = _make_prober()
        with _patch_httpx(status_code=429):
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
        # A cloud preset legitimately carries no base URL, so the skip is the
        # expected steady state rather than a misconfiguration.
        mock_config = _make_local_config(base_url=None, preset_name="ollama-cloud")

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


@pytest.mark.unit
class TestMissingBaseUrlClassification:
    """A missing base URL is by-design for cloud, a defect for self-hosted.

    Both skip the probe, so without this distinction a self-hosted provider
    persisted with no base URL is silently never probed and looks identical to
    a cloud provider working as intended.
    """

    def test_cloud_preset_does_not_require_a_base_url(self) -> None:
        config = _make_local_config(base_url=None, preset_name="ollama-cloud")
        assert _base_url_is_required(config) is False

    def test_self_hosted_preset_requires_a_base_url(self) -> None:
        config = _make_local_config(base_url=None, preset_name="ollama")
        assert _base_url_is_required(config) is True

    def test_unknown_preset_does_not_claim_a_requirement(self) -> None:
        config = _make_local_config(base_url=None, preset_name="no-such-preset")
        assert _base_url_is_required(config) is False

    def test_provider_created_without_a_preset(self) -> None:
        config = _make_local_config(base_url=None, preset_name=None)
        assert _base_url_is_required(config) is False


@pytest.mark.unit
class TestProbeProviderOnDemand:
    """A newly configured provider must not wait for the next sweep.

    Health is derived from recorded outcomes, so a provider with none reports
    UNKNOWN -- rendered identically to one that is genuinely unreachable. Until
    the mutation paths probe on write, that state persisted for up to a full
    probe interval after the operator finished configuring the provider.
    """

    async def test_probes_named_provider_immediately(self) -> None:
        prober, tracker = _make_prober()
        with _patch_httpx(status_code=200):
            await prober.probe_provider("test-local")

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.UP
        assert summary.calls_last_24h == 1

    async def test_records_a_failure_rather_than_leaving_it_unknown(self) -> None:
        prober, tracker = _make_prober()
        with _patch_httpx(side_effect=httpx.ConnectError("refused")):
            await prober.probe_provider("test-local")

        summary = await tracker.get_summary("test-local")
        assert summary.health_status == ProviderHealthStatus.DOWN

    async def test_bypasses_the_cycle_recency_guard(self) -> None:
        """A just-probed provider is re-probed: its endpoint may have changed."""
        tracker = ProviderHealthTracker()
        await tracker.record(
            ProviderHealthRecord(
                provider_name="test-local",
                timestamp=datetime.now(UTC),
                success=True,
                response_time_ms=1.0,
            )
        )
        prober, _ = _make_prober(tracker)
        # The periodic sweep skips it (probed well inside the interval) ...
        with _patch_httpx(status_code=200):
            await prober._probe_all()
        assert (await tracker.get_summary("test-local")).calls_last_24h == 1
        # ... while the on-demand probe still runs.
        with _patch_httpx(status_code=200):
            await prober.probe_provider("test-local")
        assert (await tracker.get_summary("test-local")).calls_last_24h == 2

    async def test_unconfigured_provider_records_nothing(self) -> None:
        prober, tracker = _make_prober()
        with _patch_httpx(status_code=200):
            await prober.probe_provider("never-configured")

        assert (await tracker.get_summary("never-configured")).calls_last_24h == 0

    async def test_skips_a_provider_without_a_base_url(self) -> None:
        """A cloud provider exposes no lightweight ping to send."""
        prober, tracker = _make_prober(
            configs={"cloud": _make_local_config(base_url=None)},
        )
        with _patch_httpx(status_code=200):
            await prober.probe_provider("cloud")

        assert (await tracker.get_summary("cloud")).calls_last_24h == 0

    async def test_paused_prober_does_not_probe(self) -> None:
        """The kill switch gates the on-demand path, not just the loop."""
        prober, tracker = _make_prober(enabled=False)
        with _patch_httpx(status_code=200):
            await prober.probe_provider("test-local")

        assert (await tracker.get_summary("test-local")).calls_last_24h == 0

    async def test_ssrf_blocked_provider_is_not_probed_on_demand(self) -> None:
        """The on-demand path applies the same discovery gate as the sweep.

        Covering the gate only through ``_probe_all`` would leave this path
        free to mis-thread the policy and reach an unallowlisted host.
        """
        from synthorg.providers.discovery_policy import ProviderDiscoveryPolicy

        policy = ProviderDiscoveryPolicy(host_port_allowlist=("allowed.com:8080",))

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
        with _patch_httpx(status_code=200) as ctx:
            await prober.probe_provider("test-blocked")

        assert (await tracker.get_summary("test-blocked")).calls_last_24h == 0
        assert ctx.mock_client_cls is not None
        ctx.mock_client_cls.assert_not_called()


def _resolver(result: object) -> AsyncMock:
    """Build a ``resolve_discovery_target`` double returning *result*.

    Returns:
        An AsyncMock bound to the real function's signature.
    """
    return AsyncMock(spec=resolve_discovery_target, return_value=result)


@pytest.mark.unit
class TestProbeTargetGates:
    """Each rejection reports why, and an allowlisted host is DNS-pinned.

    A silently skipped provider is indistinguishable from a healthy idle
    cycle, which is what hides a mis-scoped allowlist or a self-hosted
    provider persisted without an endpoint.
    """

    async def test_self_hosted_provider_without_a_base_url_warns(self) -> None:
        config = _make_local_config(base_url=None, preset_name="ollama")
        with capture_logs() as logs:
            target = await resolve_probe_target(
                "self-hosted", config, None, ollama_port=11434
            )

        assert target == ProbeTarget(eligible=False, validation=None)
        skipped = [entry for entry in logs if entry["event"] == PROBE_SKIPPED]
        assert [entry["log_level"] for entry in skipped] == ["warning"]
        assert skipped[0]["reason"] == "base_url_required_but_missing"

    async def test_cloud_provider_without_a_base_url_stays_at_debug(self) -> None:
        """Recurs every cycle and is not actionable, so it must not warn."""
        config = _make_local_config(base_url=None, preset_name="ollama-cloud")
        with capture_logs() as logs:
            target = await resolve_probe_target(
                "cloud", config, None, ollama_port=11434
            )

        assert target.eligible is False
        skipped = [entry for entry in logs if entry["event"] == PROBE_SKIPPED]
        assert [entry["log_level"] for entry in skipped] == ["debug"]
        assert skipped[0]["reason"] == "no_base_url"

    async def test_allowlisted_host_that_cannot_resolve_is_refused(self) -> None:
        """Probing unpinned would reopen the DNS-rebinding window."""
        policy = ProviderDiscoveryPolicy(host_port_allowlist=("host.example:8080",))
        config = _make_local_config(
            base_url="http://host.example:8080", litellm_provider=None
        )
        with (
            patch(
                "synthorg.providers.health_prober_targets.resolve_discovery_target",
                _resolver("NXDOMAIN"),
            ),
            capture_logs() as logs,
        ):
            target = await resolve_probe_target(
                "unresolvable", config, policy, ollama_port=11434
            )

        assert target == ProbeTarget(eligible=False, validation=None)
        skipped = [entry for entry in logs if entry["event"] == PROBE_SKIPPED]
        assert skipped[0]["reason"] == "discovery_dns_unresolved"

    async def test_resolved_host_carries_its_pinned_addresses(self) -> None:
        policy = ProviderDiscoveryPolicy(host_port_allowlist=("host.example:8080",))
        config = _make_local_config(
            base_url="http://host.example:8080", litellm_provider=None
        )
        validation = DnsValidationOk(
            hostname="host.example", port=8080, resolved_ips=("203.0.113.7",)
        )
        with patch(
            "synthorg.providers.health_prober_targets.resolve_discovery_target",
            _resolver(validation),
        ):
            target = await resolve_probe_target(
                "resolvable", config, policy, ollama_port=11434
            )

        # The validation must travel with the target: the probe pins its
        # connection to these IPs rather than re-resolving the hostname.
        assert target == ProbeTarget(eligible=True, validation=validation)


@pytest.mark.unit
class TestRecencyGuard:
    async def test_stale_check_does_not_suppress_the_next_probe(self) -> None:
        """A record older than one interval must not skip the provider."""
        tracker = ProviderHealthTracker()
        prober, _ = _make_prober(tracker, interval_seconds=60)
        await tracker.record(
            ProviderHealthRecord(
                provider_name="test-local",
                timestamp=datetime.now(UTC) - timedelta(seconds=120),
                success=True,
                response_time_ms=1.0,
            )
        )

        assert await prober._probed_within_interval("test-local") is False

    async def test_recent_check_suppresses_the_next_probe(self) -> None:
        tracker = ProviderHealthTracker()
        prober, _ = _make_prober(tracker, interval_seconds=3600)
        await tracker.record(
            ProviderHealthRecord(
                provider_name="test-local",
                timestamp=datetime.now(UTC),
                success=True,
                response_time_ms=1.0,
            )
        )

        assert await prober._probed_within_interval("test-local") is True
