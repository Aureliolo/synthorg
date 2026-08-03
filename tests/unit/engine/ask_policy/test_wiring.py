"""Settings-to-provider wiring for the ask policy, including its failure posture."""

import pytest

from synthorg.engine.ask_policy.provider import (
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.engine.ask_policy.wiring import (
    build_ask_policy_config,
    rebuild_and_bind_ask_policy,
)
from synthorg.settings.subscribers.ask_policy_subscriber import _WATCHED
from tests.unit.engine.ask_policy.conftest import (
    failing_settings_service,
    settings_service,
)

_EXTRAS = (
    '[{"id": "x_eng", "text": "Ask before a schema change.", '
    '"scope": "Engineering", "scope_kind": "department"}]'
)


class TestBuildConfig:
    @pytest.mark.unit
    async def test_defaults(self) -> None:
        config, complete = await build_ask_policy_config(settings_service())
        assert config.enabled is True
        assert config.extra_directives == ()
        assert complete is True

    @pytest.mark.unit
    async def test_disabled(self) -> None:
        config, _ = await build_ask_policy_config(
            settings_service(ask_policy_enabled="false")
        )
        assert config.enabled is False

    @pytest.mark.unit
    async def test_extra_directives_parse(self) -> None:
        config, _ = await build_ask_policy_config(
            settings_service(ask_policy_extra_directives=_EXTRAS)
        )
        assert [d.id for d in config.extra_directives] == ["x_eng"]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        ["not json", '{"id": "x"}', '[{"id": "x"}]', '[{"id": "", "text": "y"}]'],
    )
    async def test_malformed_extras_fail_safe_to_none(self, raw: str) -> None:
        config, complete = await build_ask_policy_config(
            settings_service(ask_policy_extra_directives=raw)
        )
        assert config.extra_directives == ()
        assert config.enabled is True
        # A malformed VALUE is not a failed READ: the operator's payload is
        # what is wrong, so there is nothing older worth keeping.
        assert complete is True

    @pytest.mark.unit
    async def test_one_bad_entry_costs_only_that_entry(self) -> None:
        raw = (
            '[{"id": "keep", "text": "Ask before a schema change.", '
            '"scope": "Engineering", "scope_kind": "department"}, {"id": ""}]'
        )
        config, _ = await build_ask_policy_config(
            settings_service(ask_policy_extra_directives=raw)
        )
        assert [d.id for d in config.extra_directives] == ["keep"]

    @pytest.mark.unit
    async def test_a_failed_read_is_reported_as_incomplete(self) -> None:
        _, complete = await build_ask_policy_config(failing_settings_service())
        assert complete is False


class TestRebuildAndBind:
    @pytest.mark.unit
    async def test_binds_the_ambient_provider(self) -> None:
        set_ask_policy_provider(None)
        await rebuild_and_bind_ask_policy(settings_service())
        assert current_ask_policy_provider() is not None

    @pytest.mark.unit
    async def test_rebind_replaces_the_previous_provider(self) -> None:
        await rebuild_and_bind_ask_policy(settings_service())
        first = current_ask_policy_provider()
        await rebuild_and_bind_ask_policy(settings_service())
        assert current_ask_policy_provider() is not first

    @pytest.mark.unit
    async def test_disabled_setting_binds_a_disabled_provider(self) -> None:
        await rebuild_and_bind_ask_policy(settings_service(ask_policy_enabled="false"))
        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is False

    @pytest.mark.unit
    async def test_cold_settings_failure_binds_the_shipped_default(self) -> None:
        # Fail to ON: for enforcement the conservative direction is to keep
        # enforcing, and for asking it is to keep asking. Nothing was bound,
        # so there is no operator choice to preserve.
        set_ask_policy_provider(None)
        bound = await rebuild_and_bind_ask_policy(failing_settings_service())
        assert bound is not None
        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is True

    @pytest.mark.unit
    async def test_a_failed_reread_keeps_the_operator_s_choice(self) -> None:
        # The subscriber re-reads right after a write, which is exactly when a
        # blip is most likely: rebinding the shipped default here would revert
        # a governance-audited "disabled" with nothing to show for it.
        await rebuild_and_bind_ask_policy(settings_service(ask_policy_enabled="false"))
        chosen = current_ask_policy_provider()

        bound = await rebuild_and_bind_ask_policy(failing_settings_service())

        assert bound is None
        assert current_ask_policy_provider() is chosen


class TestSubscriberWatchedKeys:
    @pytest.mark.unit
    def test_watches_only_the_two_ask_policy_keys(self) -> None:
        assert (
            frozenset(
                {
                    ("engine", "ask_policy_enabled"),
                    ("engine", "ask_policy_extra_directives"),
                }
            )
            == _WATCHED
        )
