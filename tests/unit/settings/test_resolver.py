"""Unit tests for ConfigResolver."""

from enum import StrEnum
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict

from synthorg.api.config import RateLimitTimeUnit
from synthorg.core.enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.models import SettingValue
from synthorg.settings.resolver import ConfigResolver, _parse_bool
from synthorg.settings.service import SettingsService
from tests.unit.settings.conftest import (
    FakeAgentConfig,
    FakeDepartment,
    FakeProviderConfig,
    make_setting_value,
)

_make_value = make_setting_value

# ── Helpers ───────────────────────────────────────────────────────


class _Color(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class _BudgetAlerts(BaseModel):
    model_config = ConfigDict(frozen=True)
    warn_at: int = 75
    critical_at: int = 90
    hard_stop_at: int = 100


class _AutoDowngrade(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    threshold: int = 85
    downgrade_map: tuple[tuple[str, str], ...] = ()
    boundary: str = "task_assignment"


class _BudgetConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_monthly: float = 100.0
    per_task_limit: float = 5.0
    per_agent_daily_limit: float = 10.0
    alerts: _BudgetAlerts = _BudgetAlerts()
    auto_downgrade: _AutoDowngrade = _AutoDowngrade()
    reset_day: int = 1


class _CoordinationSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    topology: str = "auto"
    max_concurrency_per_wave: int | None = None
    fail_fast: bool = False
    enable_workspace_isolation: bool = True
    base_branch: str = "main"


class _FakeAuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    jwt_secret: str = ""
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_expiry_minutes: int = 1440
    min_password_length: int = 12
    exclude_paths: tuple[str, ...] | None = None


class _FakeRateLimitConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    unauth_max_requests: int = 20
    auth_max_requests: int = 6000
    time_unit: RateLimitTimeUnit = RateLimitTimeUnit.MINUTE
    exclude_paths: tuple[str, ...] = ("/api/v1/healthz", "/api/v1/readyz")


class _FakeCorsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)


class _FakeServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str = "127.0.0.1"
    port: int = 3001


class _FakeApiConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    cors: _FakeCorsConfig = _FakeCorsConfig()
    rate_limit: _FakeRateLimitConfig = _FakeRateLimitConfig()
    server: _FakeServerConfig = _FakeServerConfig()
    auth: _FakeAuthConfig = _FakeAuthConfig()
    api_prefix: NotBlankStr = "/api/v1"


class _FakeRootConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    api: _FakeApiConfig = _FakeApiConfig()
    budget: _BudgetConfig = _BudgetConfig()
    coordination: _CoordinationSection = _CoordinationSection()
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


# ── Scalar Accessor Tests ────────────────────────────────────────


@pytest.mark.unit
class TestGetStr:
    """Tests for get_str()."""

    async def test_returns_string_value(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("hello")
        result = await resolver.get_str("test", "key")
        assert result == "hello"
        mock_settings.get.assert_awaited_once_with("test", "key")

    async def test_returns_empty_string(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("")
        result = await resolver.get_str("test", "key")
        assert result == ""

    async def test_not_found_logs_and_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_str("bad", "key")


@pytest.mark.unit
class TestGetInt:
    """Tests for get_int()."""

    async def test_parses_positive_int(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("42")
        assert await resolver.get_int("test", "key") == 42

    async def test_parses_negative_int(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("-7")
        assert await resolver.get_int("test", "key") == -7

    async def test_parses_zero(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("0")
        assert await resolver.get_int("test", "key") == 0

    async def test_invalid_raises_value_error(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("not_a_number")
        with pytest.raises(ValueError, match="invalid integer"):
            await resolver.get_int("test", "key")


@pytest.mark.unit
class TestGetFloat:
    """Tests for get_float()."""

    async def test_parses_float(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("3.14")
        result = await resolver.get_float("test", "key")
        assert result == pytest.approx(3.14)

    async def test_parses_integer_string_as_float(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("100")
        assert await resolver.get_float("test", "key") == 100.0

    async def test_invalid_raises_value_error(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("abc")
        with pytest.raises(ValueError, match="invalid float"):
            await resolver.get_float("test", "key")


@pytest.mark.unit
class TestGetBool:
    """Tests for get_bool()."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
        ],
    )
    async def test_parses_valid_values(
        self,
        resolver: ConfigResolver,
        mock_settings: AsyncMock,
        raw: str,
        expected: bool,
    ) -> None:
        mock_settings.get.return_value = _make_value(raw)
        assert await resolver.get_bool("test", "key") is expected

    async def test_invalid_raises_value_error(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("maybe")
        with pytest.raises(ValueError, match="not a recognized boolean"):
            await resolver.get_bool("test", "key")


@pytest.mark.unit
class TestGetEnum:
    """Tests for get_enum()."""

    async def test_parses_valid_enum(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("red")
        result = await resolver.get_enum("test", "key", _Color)
        assert result is _Color.RED

    async def test_invalid_raises_value_error(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value("purple")
        with pytest.raises(ValueError, match=r"invalid.*_Color"):
            await resolver.get_enum("test", "key", _Color)


# ── Error Propagation ─────────────────────────────────────────────


@pytest.mark.unit
class TestErrorPropagation:
    """SettingNotFoundError should propagate from SettingsService."""

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.side_effect = SettingNotFoundError("nope")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_str("bad", "key")


# ── Constructor Guard ─────────────────────────────────────────────


@pytest.mark.unit
class TestConfigResolverInit:
    """Tests for ConfigResolver constructor validation."""

    def test_none_settings_service_raises_type_error(
        self,
        root_config: _FakeRootConfig,
    ) -> None:
        with pytest.raises(TypeError, match="settings_service must not be None"):
            ConfigResolver(
                settings_service=None,  # type: ignore[arg-type]
                config=root_config,  # type: ignore[arg-type]
            )


# ── Composed Read: Autonomy Level ─────────────────────────────────


@pytest.mark.unit
class TestGetAutonomyLevel:
    """Tests for get_autonomy_level()."""

    async def test_resolves_autonomy_level(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get.return_value = _make_value(
            "supervised", namespace=SettingNamespace.COMPANY, key="autonomy_level"
        )
        result = await resolver.get_autonomy_level()
        assert result is AutonomyLevel.SUPERVISED
        mock_settings.get.assert_awaited_once_with("company", "autonomy_level")

    @pytest.mark.parametrize("level", list(AutonomyLevel))
    async def test_resolves_all_levels(
        self,
        resolver: ConfigResolver,
        mock_settings: AsyncMock,
        level: AutonomyLevel,
    ) -> None:
        mock_settings.get.return_value = _make_value(level.value)
        result = await resolver.get_autonomy_level()
        assert result is level


# ── Composed Read: Budget Config ──────────────────────────────────


def _budget_get_side_effect(
    overrides: dict[tuple[str, str], str] | None = None,
) -> AsyncMock:
    """Create a mock .get() that returns budget defaults with optional overrides."""
    defaults = {
        ("budget", "total_monthly"): "100.0",
        ("budget", "per_task_limit"): "5.0",
        ("budget", "per_agent_daily_limit"): "10.0",
        ("budget", "auto_downgrade_enabled"): "false",
        ("budget", "auto_downgrade_threshold"): "85",
        ("budget", "reset_day"): "1",
        ("budget", "alert_warn_at"): "75",
        ("budget", "alert_critical_at"): "90",
        ("budget", "alert_hard_stop_at"): "100",
        ("budget", "currency"): "USD",
    }
    merged = {**defaults, **(overrides or {})}

    async def _get(ns: str, key: str) -> SettingValue:
        value = merged.get((ns, key))
        if value is None:
            msg = f"Unknown: {ns}/{key}"
            raise SettingNotFoundError(msg)
        return _make_value(value, namespace=SettingNamespace(ns), key=key)

    return AsyncMock(side_effect=_get)


@pytest.mark.unit
class TestGetBudgetConfig:
    """Tests for get_budget_config() composed read."""

    async def test_returns_budget_config_from_defaults(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _budget_get_side_effect()
        result = await resolver.get_budget_config()

        assert result.total_monthly == 100.0
        assert result.per_task_limit == 5.0
        assert result.per_agent_daily_limit == 10.0
        assert result.auto_downgrade.enabled is False
        assert result.auto_downgrade.threshold == 85
        assert result.reset_day == 1
        assert result.alerts.warn_at == 75
        assert result.alerts.critical_at == 90
        assert result.alerts.hard_stop_at == 100
        assert result.currency == "USD"

    async def test_db_overrides_take_precedence(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _budget_get_side_effect(
            {
                ("budget", "total_monthly"): "500.0",
                ("budget", "per_task_limit"): "25.0",
                ("budget", "auto_downgrade_enabled"): "true",
            }
        )
        result = await resolver.get_budget_config()

        assert result.total_monthly == 500.0
        assert result.per_task_limit == 25.0
        assert result.auto_downgrade.enabled is True
        # Non-overridden fields keep defaults
        assert result.per_agent_daily_limit == 10.0
        assert result.auto_downgrade.threshold == 85

    async def test_preserves_unregistered_fields(
        self,
        mock_settings: AsyncMock,
    ) -> None:
        """Unregistered fields (downgrade_map, boundary) keep YAML values."""
        custom_config = _FakeRootConfig(
            budget=_BudgetConfig(
                auto_downgrade=_AutoDowngrade(
                    downgrade_map=(("large", "small"),),
                    boundary="task_assignment",
                ),
            ),
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=custom_config,  # type: ignore[arg-type]
        )
        mock_settings.get = _budget_get_side_effect()
        result = await resolver.get_budget_config()

        assert result.auto_downgrade.downgrade_map == (("large", "small"),)
        assert result.auto_downgrade.boundary == "task_assignment"

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """SettingNotFoundError propagates directly (unwrapped from ExceptionGroup)."""
        mock_settings.get.side_effect = SettingNotFoundError("missing")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_budget_config()

    async def test_value_error_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """ValueError from a corrupted DB value propagates directly."""
        mock_settings.get = _budget_get_side_effect(
            {("budget", "total_monthly"): "not-a-number"}
        )
        with pytest.raises(ValueError, match="invalid"):
            await resolver.get_budget_config()


# ── Composed Read: Coordination Config ────────────────────────────


def _coordination_get_side_effect(
    overrides: dict[tuple[str, str], str] | None = None,
) -> AsyncMock:
    """Create a mock .get() for coordination settings."""
    defaults = {
        ("coordination", "max_concurrency_per_wave"): "5",
        ("coordination", "fail_fast"): "false",
        ("coordination", "enable_workspace_isolation"): "true",
        ("coordination", "base_branch"): "main",
    }
    merged = {**defaults, **(overrides or {})}

    async def _get(ns: str, key: str) -> SettingValue:
        value = merged.get((ns, key))
        if value is None:
            msg = f"Unknown: {ns}/{key}"
            raise SettingNotFoundError(msg)
        return _make_value(value, namespace=SettingNamespace(ns), key=key)

    return AsyncMock(side_effect=_get)


@pytest.mark.unit
class TestGetCoordinationConfig:
    """Tests for get_coordination_config() composed read."""

    async def test_returns_config_from_defaults(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _coordination_get_side_effect()
        result = await resolver.get_coordination_config()

        assert result.max_concurrency_per_wave == 5
        assert result.fail_fast is False
        assert result.enable_workspace_isolation is True
        assert result.base_branch == "main"

    async def test_request_overrides_take_precedence(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _coordination_get_side_effect()
        result = await resolver.get_coordination_config(
            max_concurrency_per_wave=10,
            fail_fast=True,
        )

        assert result.max_concurrency_per_wave == 10
        assert result.fail_fast is True
        # Non-overridden settings stay
        assert result.enable_workspace_isolation is True
        assert result.base_branch == "main"

    async def test_db_overrides_via_settings(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _coordination_get_side_effect(
            {
                ("coordination", "fail_fast"): "true",
                ("coordination", "base_branch"): "develop",
            }
        )
        result = await resolver.get_coordination_config()

        assert result.fail_fast is True
        assert result.base_branch == "develop"

    async def test_request_overrides_beat_db_overrides(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """Request-level overrides beat DB overrides for supported fields."""
        mock_settings.get = _coordination_get_side_effect(
            {("coordination", "fail_fast"): "true"}
        )
        result = await resolver.get_coordination_config(fail_fast=False)
        assert result.fail_fast is False

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """SettingNotFoundError propagates directly (unwrapped from ExceptionGroup)."""
        mock_settings.get.side_effect = SettingNotFoundError("missing")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_coordination_config()

    async def test_value_error_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """ValueError from a corrupted coordination value propagates directly."""
        mock_settings.get = _coordination_get_side_effect(
            {("coordination", "max_concurrency_per_wave"): "not-a-number"}
        )
        with pytest.raises(ValueError, match="invalid"):
            await resolver.get_coordination_config()


# ── _parse_bool Tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestParseBool:
    """Tests for the _parse_bool helper."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
        ],
    )
    def test_valid_values(self, raw: str, expected: bool) -> None:
        assert _parse_bool(raw) is expected

    @pytest.mark.parametrize("raw", ["yes", "no", "maybe", "", "2", "tru"])
    def test_invalid_values(self, raw: str) -> None:
        with pytest.raises(ValueError, match="not a recognized boolean"):
            _parse_bool(raw)


# ── Property-Based Tests (Hypothesis) ─────────────────────────────


def _make_resolver() -> tuple[ConfigResolver, AsyncMock]:
    """Create a fresh resolver + mock for property-based tests."""
    mock = AsyncMock(spec=SettingsService)
    config = _FakeRootConfig()
    resolver = ConfigResolver(
        settings_service=mock,
        config=config,  # type: ignore[arg-type]
    )
    return resolver, mock


@pytest.mark.unit
class TestResolverScalarProperties:
    """Hypothesis-based roundtrip tests for scalar accessors."""

    @given(st.integers(min_value=-(2**31), max_value=2**31))
    @settings()
    async def test_int_roundtrip(self, n: int) -> None:
        resolver, mock = _make_resolver()
        mock.get = AsyncMock(return_value=_make_value(str(n)))
        assert await resolver.get_int("budget", "total_monthly") == n

    @given(st.floats(allow_nan=False, allow_infinity=False, allow_subnormal=False))
    @settings()
    async def test_float_roundtrip(self, x: float) -> None:
        resolver, mock = _make_resolver()
        mock.get = AsyncMock(return_value=_make_value(str(x)))
        result = await resolver.get_float("budget", "total_monthly")
        assert result == pytest.approx(x, rel=1e-9, abs=1e-15)

    @given(st.booleans())
    @settings()
    async def test_bool_roundtrip(self, b: bool) -> None:
        resolver, mock = _make_resolver()
        mock.get = AsyncMock(return_value=_make_value(str(b)))
        assert await resolver.get_bool("budget", "total_monthly") is b


# ── Composed Read: API Config ────────────────────────────────────


def _api_get_side_effect(
    overrides: dict[tuple[str, str], str] | None = None,
) -> AsyncMock:
    """Create a mock .get() that returns API defaults with optional overrides."""
    defaults = {
        ("api", "rate_limit_unauth_max_requests"): "20",
        ("api", "rate_limit_auth_max_requests"): "6000",
        ("api", "rate_limit_time_unit"): "minute",
        ("api", "jwt_expiry_minutes"): "1440",
        ("api", "min_password_length"): "12",
    }
    merged = {**defaults, **(overrides or {})}

    async def _get(ns: str, key: str) -> SettingValue:
        value = merged.get((ns, key))
        if value is None:
            msg = f"Unknown: {ns}/{key}"
            raise SettingNotFoundError(msg)
        return _make_value(value, namespace=SettingNamespace(ns), key=key)

    return AsyncMock(side_effect=_get)


@pytest.mark.unit
class TestGetApiConfig:
    """Tests for get_api_config() composed read."""

    async def test_returns_api_config_from_defaults(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _api_get_side_effect()
        result = await resolver.get_api_config()

        assert result.rate_limit.unauth_max_requests == 20
        assert result.rate_limit.time_unit == RateLimitTimeUnit.MINUTE
        assert result.auth.jwt_expiry_minutes == 1440
        assert result.auth.min_password_length == 12

    async def test_db_overrides_take_precedence(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        mock_settings.get = _api_get_side_effect(
            {
                ("api", "rate_limit_unauth_max_requests"): "50",
                ("api", "rate_limit_auth_max_requests"): "1000",
                ("api", "min_password_length"): "16",
            }
        )
        result = await resolver.get_api_config()

        assert result.rate_limit.unauth_max_requests == 50
        assert result.rate_limit.auth_max_requests == 1000
        assert result.auth.min_password_length == 16
        # Non-overridden fields keep defaults
        assert result.rate_limit.time_unit == RateLimitTimeUnit.MINUTE
        assert result.auth.jwt_expiry_minutes == 1440

    async def test_preserves_unregistered_fields(
        self,
        mock_settings: AsyncMock,
    ) -> None:
        """Bootstrap-only fields keep YAML values.

        Covers CORS, server, api_prefix, and exclude_paths.
        """
        custom_config = _FakeRootConfig(
            api=_FakeApiConfig(
                cors=_FakeCorsConfig(allowed_origins=("https://example.com",)),
                server=_FakeServerConfig(host="10.0.0.1", port=9000),
                rate_limit=_FakeRateLimitConfig(
                    exclude_paths=("/health", "/custom"),
                ),
                auth=_FakeAuthConfig(exclude_paths=("/public",)),
                api_prefix="/api/v2",
            ),
        )
        resolver = ConfigResolver(
            settings_service=mock_settings,
            config=custom_config,  # type: ignore[arg-type]
        )
        mock_settings.get = _api_get_side_effect()
        result = await resolver.get_api_config()

        assert result.cors.allowed_origins == ("https://example.com",)
        assert result.server.host == "10.0.0.1"
        assert result.server.port == 9000
        assert result.rate_limit.exclude_paths == ("/health", "/custom")
        assert result.auth.exclude_paths == ("/public",)
        assert result.api_prefix == "/api/v2"

    async def test_not_found_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """SettingNotFoundError unwrapped from ExceptionGroup."""
        mock_settings.get.side_effect = SettingNotFoundError("missing")
        with pytest.raises(SettingNotFoundError):
            await resolver.get_api_config()

    async def test_value_error_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """ValueError from a corrupted DB value propagates directly."""
        mock_settings.get = _api_get_side_effect(
            {("api", "rate_limit_unauth_max_requests"): "not-a-number"}
        )
        with pytest.raises(ValueError, match="invalid"):
            await resolver.get_api_config()

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("second", RateLimitTimeUnit.SECOND),
            ("minute", RateLimitTimeUnit.MINUTE),
            ("hour", RateLimitTimeUnit.HOUR),
            ("day", RateLimitTimeUnit.DAY),
        ],
    )
    async def test_enum_resolution(
        self,
        resolver: ConfigResolver,
        mock_settings: AsyncMock,
        value: str,
        expected: RateLimitTimeUnit,
    ) -> None:
        """rate_limit_time_unit resolves to the correct enum member."""
        mock_settings.get = _api_get_side_effect(
            {("api", "rate_limit_time_unit"): value}
        )
        result = await resolver.get_api_config()

        assert result.rate_limit.time_unit == expected

    async def test_partial_failure_unwraps_first_exception(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """When one setting fails but others succeed, first exception propagates."""
        call_count = 0

        async def _mixed_get(ns: str, key: str) -> SettingValue:
            nonlocal call_count
            call_count += 1
            if key == "jwt_expiry_minutes":
                msg = "jwt_expiry_minutes"
                raise SettingNotFoundError(msg)
            defaults = {
                ("api", "rate_limit_unauth_max_requests"): "20",
                ("api", "rate_limit_auth_max_requests"): "6000",
                ("api", "rate_limit_time_unit"): "minute",
                ("api", "min_password_length"): "12",
            }
            value = defaults.get((ns, key))
            if value is None:
                msg = f"Unknown: {ns}/{key}"
                raise SettingNotFoundError(msg)
            return _make_value(value, namespace=SettingNamespace(ns), key=key)

        mock_settings.get = AsyncMock(side_effect=_mixed_get)
        with pytest.raises(SettingNotFoundError, match="jwt_expiry_minutes"):
            await resolver.get_api_config()

    async def test_invalid_enum_value_propagates(
        self, resolver: ConfigResolver, mock_settings: AsyncMock
    ) -> None:
        """Invalid enum value for rate_limit_time_unit raises ValueError."""
        mock_settings.get = _api_get_side_effect(
            {("api", "rate_limit_time_unit"): "weekly"}
        )
        with pytest.raises(ValueError, match=r"invalid.*RateLimitTimeUnit"):
            await resolver.get_api_config()
