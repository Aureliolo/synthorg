"""Tests for reading the persisted ``providers.configs`` envelope.

Every case here is a behaviour that shipped broken: one rejected nested
field dropped every provider, and the resulting empty map was reported as
a first-run empty company.
"""

import pytest
from pydantic import ValidationError

from synthorg.config.provider_configs_read import (
    PROVIDERS_CONFIG_SCHEMA_VERSION,
    ProviderConfigsStatus,
    read_provider_configs,
)
from synthorg.config.provider_schema import ProviderConfig


def _entry(**overrides: object) -> dict[str, object]:
    """Return a minimal valid provider-config entry."""
    return {"connection_name": "conn-a", **overrides}


def _envelope(providers: object, version: int | None = None) -> dict[str, object]:
    """Return an envelope wrapping *providers*."""
    return {
        "schema_version": (
            PROVIDERS_CONFIG_SCHEMA_VERSION if version is None else version
        ),
        "providers": providers,
    }


@pytest.mark.unit
class TestReadProviderConfigs:
    def test_valid_envelope_reads_every_provider(self) -> None:
        result = read_provider_configs(
            _envelope({"alpha": _entry(), "beta": _entry()}), {}
        )

        assert result.status is ProviderConfigsStatus.OK
        assert sorted(result.providers) == ["alpha", "beta"]
        assert result.rejected == ()
        assert result.coerced == ()

    def test_one_bad_entry_does_not_cost_the_others(self) -> None:
        """The regression: a rejected entry costs that entry, not the set."""
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(),
                    "beta": _entry(max_context="not-a-number", driver=""),
                    "gamma": _entry(),
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.PARTIAL
        assert sorted(result.providers) == ["alpha", "gamma"]
        assert [rejected.name for rejected in result.rejected] == ["beta"]

    def test_rejection_reason_does_not_leak_the_entry(self) -> None:
        """Provider entries carry credentials, so reasons are redacted."""
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(
                        auth_type="oauth",
                        oauth_client_secret="sk-not-a-real-secret-value",
                    )
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert "sk-not-a-real-secret-value" not in result.rejected[0].reason

    def test_retired_fallback_providers_key_is_stripped_not_rejected(self) -> None:
        """The live failure: a retired key cost the operator every provider."""
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(
                        degradation={
                            "strategy": "alert",
                            "fallback_providers": ["beta"],
                            "queue_max_wait_seconds": 300,
                        }
                    ),
                    "beta": _entry(),
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.OK
        assert sorted(result.providers) == ["alpha", "beta"]
        assert result.rejected == ()
        assert [(c.name, c.setting) for c in result.coerced] == [
            ("alpha", "fallback_providers")
        ]

    def test_retired_fallback_strategy_is_coerced(self) -> None:
        result = read_provider_configs(
            _envelope({"alpha": _entry(degradation={"strategy": "fallback"})}), {}
        )

        assert result.status is ProviderConfigsStatus.OK
        assert [(c.name, c.setting) for c in result.coerced] == [("alpha", "strategy")]

    def test_every_entry_invalid_is_unreadable_not_an_empty_success(self) -> None:
        """An unreadable config must not read as a first-run empty company."""
        fallback = {"from-code": ProviderConfig(connection_name="conn-a")}
        result = read_provider_configs(
            _envelope({"alpha": _entry(driver=""), "beta": _entry(driver="")}),
            fallback,
        )

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert result.providers == fallback
        assert sorted(rejected.name for rejected in result.rejected) == [
            "alpha",
            "beta",
        ]

    def test_no_providers_persisted_is_a_clean_empty_read(self) -> None:
        """The one remaining shape that genuinely means "first run"."""
        result = read_provider_configs(_envelope({}), {})

        assert result.status is ProviderConfigsStatus.OK
        assert result.providers == {}

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("not-a-mapping", id="not-a-dict"),
            pytest.param([{"schema_version": 1}], id="list"),
            pytest.param({"providers": {}}, id="no-schema-version"),
            pytest.param({"schema_version": "one", "providers": {}}, id="bad-version"),
        ],
    )
    def test_unusable_envelope_shape_is_unreadable(self, raw: object) -> None:
        fallback = {"from-code": ProviderConfig(connection_name="conn-a")}

        result = read_provider_configs(raw, fallback)

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert result.providers == fallback

    def test_unknown_schema_version_is_unreadable(self) -> None:
        """A rollback after a schema bump reaches this with no stale data."""
        fallback = {"from-code": ProviderConfig(connection_name="conn-a")}

        result = read_provider_configs(
            _envelope({"alpha": _entry()}, version=PROVIDERS_CONFIG_SCHEMA_VERSION + 1),
            fallback,
        )

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert result.providers == fallback

    def test_providers_not_a_mapping_is_unreadable(self) -> None:
        result = read_provider_configs(_envelope(["alpha"]), {})

        assert result.status is ProviderConfigsStatus.UNREADABLE


@pytest.mark.unit
class TestRetiredSettingsStillRefusedOnWrite:
    """The read coerces; the write must keep refusing.

    Guards the split against a later simplification that moves the
    coercion into the model and silently accepts an operator writing a
    setting the system no longer honours.
    """

    def test_constructing_a_config_with_the_retired_key_still_raises(self) -> None:
        with pytest.raises(ValidationError, match="fallback_providers"):
            ProviderConfig(
                connection_name="conn-a",
                degradation={"fallback_providers": ["beta"]},  # type: ignore[arg-type]
            )

    def test_constructing_a_config_with_the_retired_strategy_still_raises(self) -> None:
        with pytest.raises(ValidationError, match="fallback"):
            ProviderConfig(
                connection_name="conn-a",
                degradation={"strategy": "fallback"},  # type: ignore[arg-type]
            )
