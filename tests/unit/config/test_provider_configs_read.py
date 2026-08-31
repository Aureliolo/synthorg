"""Tests for reading the persisted ``providers.configs`` envelope.

Each case guards a way a persisted blob can be partly or wholly unreadable:
one rejected nested field must cost only the entry carrying it, never the
whole provider map, and an unreadable blob must never be reported as a
first-run empty company.
"""

import pytest
import structlog.testing
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
        """A rejected entry costs that entry, not the whole provider set."""
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

    @pytest.mark.parametrize(
        "secret",
        [
            pytest.param("sk-not-a-real-secret-value", id="vendor-prefixed"),
            # The shape a pattern scrubber cannot recognise, and the one this
            # product must assume: nothing here privileges a vendor, so an
            # operator's key is as likely to be a self-hosted gateway's
            # opaque string as an issued token with a known prefix. A test
            # that only ever passes a recognisable secret proves the narrow
            # case and hides the general one.
            pytest.param("gateway-9f2c1a8b7d6e5f4a3b2c1d0e", id="unrecognisable"),
        ],
    )
    def test_rejection_reason_never_carries_the_credential(self, secret: str) -> None:
        """Provider entries carry credentials, so reasons never quote them."""
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(),
                    "beta": _entry(auth_type="oauth", oauth_client_secret=secret),
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.PARTIAL
        assert [rejected.name for rejected in result.rejected] == ["beta"]
        assert secret not in result.rejected[0].reason

    @pytest.mark.parametrize(
        "secret",
        [
            pytest.param("sk-not-a-real-secret-value", id="vendor-prefixed"),
            pytest.param("gateway-9f2c1a8b7d6e5f4a3b2c1d0e", id="unrecognisable"),
        ],
    )
    def test_envelope_detail_never_carries_a_credential(self, secret: str) -> None:
        """The envelope-level detail reaches an external notification sink.

        A pydantic error over the whole blob quotes the whole blob, so this
        is the one description in the module that could carry every
        provider's credentials off the machine at once.
        """
        result = read_provider_configs(
            {"providers": {"beta": {"oauth_client_secret": secret}}},
            {},
        )

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert result.detail is not None
        assert secret not in result.detail

    def test_retired_fallback_providers_key_is_stripped_not_rejected(self) -> None:
        """A retired key is stripped, not rejected, so it costs no provider."""
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

    def test_retired_defaults_key_is_stripped_not_rejected(self) -> None:
        """A persisted ``defaults`` block is dropped whole, not rejected.

        ``ProviderModelDefaults`` is retired: its one field moved to a
        per-model derivation, so a config carrying the old block must not
        cost the provider its whole entry under ``extra=\"forbid\"``.
        """
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(defaults={"fallback_max_output_tokens": 8192}),
                    "beta": _entry(),
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.OK
        assert sorted(result.providers) == ["alpha", "beta"]
        assert result.rejected == ()
        assert [(c.name, c.setting) for c in result.coerced] == [("alpha", "defaults")]

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
        ("entry", "case"),
        [
            pytest.param("not-a-dict-at-all", "string", id="string-entry"),
            pytest.param(None, "null", id="null-entry"),
            pytest.param(["a", "list"], "list", id="list-entry"),
            pytest.param(42, "number", id="number-entry"),
        ],
    )
    def test_a_malformed_entry_costs_only_itself(
        self, entry: object, case: str
    ) -> None:
        """A partial write or a hand-edited row leaves shapes like these.

        They fail before any field of theirs is looked at, so the envelope
        is where they would take the whole map down if the container's own
        type judged entry shape.
        """
        result = read_provider_configs(
            _envelope({"alpha": _entry(), "broken": entry}), {}
        )

        assert result.status is ProviderConfigsStatus.PARTIAL, case
        assert sorted(result.providers) == ["alpha"]
        assert [rejected.name for rejected in result.rejected] == ["broken"]

    def test_a_blank_provider_name_costs_only_itself(self) -> None:
        """Nothing can be bound to a nameless connection, but the rest can."""
        result = read_provider_configs(_envelope({"alpha": _entry(), "": _entry()}), {})

        assert result.status is ProviderConfigsStatus.PARTIAL
        assert sorted(result.providers) == ["alpha"]
        assert [rejected.name for rejected in result.rejected] == [""]

    def test_one_entry_coerced_while_another_is_rejected(self) -> None:
        """Coercion is computed up front and carried through the entry pass.

        The two are collected in different places, so an entry needing only
        a strip and an entry that is genuinely unreadable have to arrive in
        the same result without either erasing the other.
        """
        result = read_provider_configs(
            _envelope(
                {
                    "alpha": _entry(
                        degradation={"fallback_providers": ["beta"]},
                    ),
                    "beta": _entry(driver=""),
                }
            ),
            {},
        )

        assert result.status is ProviderConfigsStatus.PARTIAL
        assert sorted(result.providers) == ["alpha"]
        assert [rejected.name for rejected in result.rejected] == ["beta"]
        assert [(c.name, c.setting) for c in result.coerced] == [
            ("alpha", "fallback_providers")
        ]

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
class TestAnUnreadableBlobIsReportedNotLogged:
    """This read runs on every provider lookup.

    A stale blob changes only when an operator edits it, so a log here
    fires once per lookup for a condition that never moves and buries the
    log for the life of the process. It is the reason a coerced setting is
    reported rather than logged, and it applies no less to an envelope
    nothing could be made of.
    """

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            pytest.param("not-a-mapping", "expected_dict", id="not-a-mapping"),
            pytest.param(
                {
                    "schema_version": PROVIDERS_CONFIG_SCHEMA_VERSION + 1,
                    "providers": {},
                },
                "unknown_schema_version",
                id="unknown-version",
            ),
        ],
    )
    def test_the_read_itself_logs_nothing(self, raw: object, reason: str) -> None:
        with structlog.testing.capture_logs() as logs:
            result = read_provider_configs(raw, {})

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert logs == []
        # What the log used to carry travels to the caller instead, so the
        # reporting caller can say it once without reconstructing it.
        assert result.reason == reason
        assert result.detail is not None

    def test_every_entry_rejected_also_names_its_reason(self) -> None:
        result = read_provider_configs(
            _envelope({"alpha": _entry(driver="")}),
            {},
        )

        assert result.status is ProviderConfigsStatus.UNREADABLE
        assert result.reason == "no_entry_validated"


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
