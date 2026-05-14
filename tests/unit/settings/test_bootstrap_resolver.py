"""Coverage for ``synthorg.settings.bootstrap_resolver.resolve_init_value``.

The helper is the sanctioned pre-``SettingsService`` resolver for
Category-2 settings consumed at app construction time (rate-limiter
middleware, log directory, log level, etc.).  Reads the registry
metadata for the env-var name (override or auto-derived) and the
typed default, then applies env > default with an optional parse
callback.
"""

import pytest

from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.bootstrap_resolver import (
    BootstrapResolvedValue,
    resolve_init_value,
)
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError

pytestmark = pytest.mark.unit


def test_env_present_no_parse_returns_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env var is set, helper returns the env value with ENVIRONMENT source."""
    monkeypatch.setenv("SYNTHORG_LOG_DIR", "/var/log/synthorg-test")
    resolved = resolve_init_value(
        SettingNamespace.OBSERVABILITY,
        "log_directory",
    )
    assert resolved == BootstrapResolvedValue(
        value="/var/log/synthorg-test",
        source=SettingSource.ENVIRONMENT,
    )


def test_env_empty_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty env var is treated as absent; default applies."""
    monkeypatch.setenv("SYNTHORG_LOG_DIR", "")
    resolved = resolve_init_value(
        SettingNamespace.OBSERVABILITY,
        "log_directory",
    )
    assert resolved.source == SettingSource.DEFAULT


def test_env_unset_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env var is unset, helper returns the registered default."""
    monkeypatch.delenv("SYNTHORG_LOG_DIR", raising=False)
    resolved = resolve_init_value(
        SettingNamespace.OBSERVABILITY,
        "log_directory",
    )
    assert resolved.source == SettingSource.DEFAULT


def test_env_present_with_parse_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse callback transforms the env string and short-circuits the chain."""
    monkeypatch.setenv("SYNTHORG_API_RATE_LIMITER_ENABLED", "true")

    def parse_bool(raw: str) -> bool | None:
        token = raw.strip().lower()
        if token in ("true", "1", "yes"):
            return True
        if token in ("false", "0", "no"):
            return False
        return None

    resolved = resolve_init_value(
        SettingNamespace.API,
        "rate_limiter_enabled",
        parse=parse_bool,
    )
    assert resolved.value is True
    assert resolved.source == SettingSource.ENVIRONMENT


def test_env_present_parse_returns_none_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse callback returning None signals invalid input; default applies."""
    monkeypatch.setenv("SYNTHORG_API_RATE_LIMITER_ENABLED", "xyzzy")

    def parse_bool(raw: str) -> bool | None:
        token = raw.strip().lower()
        if token in ("true", "1", "yes"):
            return True
        if token in ("false", "0", "no"):
            return False
        return None

    resolved = resolve_init_value(
        SettingNamespace.API,
        "rate_limiter_enabled",
        parse=parse_bool,
    )
    assert resolved.source == SettingSource.DEFAULT
    assert resolved.value is True  # api.rate_limiter_enabled default is "true"


def test_unknown_setting_raises_not_found() -> None:
    """Unregistered (namespace, key) raises SettingNotFoundError."""
    with pytest.raises(SettingNotFoundError):
        resolve_init_value(SettingNamespace.API, "this_key_does_not_exist")


def test_env_var_override_takes_precedence_over_auto_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env_var_override is set, the override name is consulted.

    The auto-derived name must NOT be consulted when an override is
    declared. ``communication.nats_url`` has
    ``env_var_override="SYNTHORG_NATS_URL"``.
    Setting the auto-derived ``SYNTHORG_COMMUNICATION_NATS_URL`` must
    NOT be consulted.
    """
    monkeypatch.setenv("SYNTHORG_COMMUNICATION_NATS_URL", "nats://wrong:4222")
    monkeypatch.setenv("SYNTHORG_NATS_URL", "nats://right:4222")
    resolved = resolve_init_value(
        SettingNamespace.COMMUNICATION,
        "nats_url",
    )
    assert resolved.value == "nats://right:4222"
    assert resolved.source == SettingSource.ENVIRONMENT


def test_read_only_post_init_resolves_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read_only_post_init=True`` does not change pre-init behaviour.

    The flag is enforced post-init by SettingsService; the bootstrap
    resolver applies env > default uniformly.
    """
    monkeypatch.setenv("SYNTHORG_API_SERVER_PORT", "9999")
    resolved = resolve_init_value(SettingNamespace.API, "server_port")
    assert resolved.value == "9999"
    assert resolved.source == SettingSource.ENVIRONMENT


def test_parse_returning_value_overrides_default_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse callback's return type becomes the resolved value type.

    The helper is generic: parse can return int, bool, list, etc.
    """
    monkeypatch.setenv("SYNTHORG_API_SERVER_PORT", "3001")
    resolved = resolve_init_value(
        SettingNamespace.API,
        "server_port",
        parse=int,
    )
    assert resolved.value == 3001
    assert isinstance(resolved.value, int)


def test_default_returned_as_string_without_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env unset and no parse, default is returned as the raw registered string."""
    monkeypatch.delenv("SYNTHORG_API_SERVER_PORT", raising=False)
    resolved = resolve_init_value(SettingNamespace.API, "server_port")
    assert resolved.value == "3001"  # registered default string
    assert resolved.source == SettingSource.DEFAULT


def test_default_passed_through_parse_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env unset, the registered default is passed through parse if supplied."""
    monkeypatch.delenv("SYNTHORG_API_SERVER_PORT", raising=False)
    resolved = resolve_init_value(
        SettingNamespace.API,
        "server_port",
        parse=int,
    )
    assert resolved.value == 3001
    assert resolved.source == SettingSource.DEFAULT


def test_custom_env_mapping_supported() -> None:
    """The ``env`` parameter accepts a custom Mapping (not just os.environ)."""
    custom_env = {"SYNTHORG_LOG_DIR": "/custom/path"}
    resolved = resolve_init_value(
        SettingNamespace.OBSERVABILITY,
        "log_directory",
        env=custom_env,
    )
    assert resolved.value == "/custom/path"
    assert resolved.source == SettingSource.ENVIRONMENT
