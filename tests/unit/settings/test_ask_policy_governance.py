"""Security-write governance for the three human-ask toggles.

Turning any of them off removes the only in-run path by which an agent defers a
material, hard-to-reverse choice to a human, so the disabling direction is a
deliberate act.
"""

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)
from synthorg.settings.write_governance_policy import is_guarded, is_weakening

_NS = "engine"
_ASK_KEYS = ("ask_policy_enabled", "clarification_enabled", "scoping_enabled")
_EXTRAS = "ask_policy_extra_directives"


class TestGuarded:
    @pytest.mark.unit
    @pytest.mark.parametrize("key", _ASK_KEYS)
    def test_ask_toggle_is_guarded(self, key: str) -> None:
        assert is_guarded(_NS, key) is True

    @pytest.mark.unit
    def test_extra_directives_are_guarded(self) -> None:
        # An added directive renders directly beneath the standing one, so
        # "never escalate schema decisions, decide them yourself" neutralises
        # the deferral posture org-wide by a different edit than the toggle.
        assert is_guarded(_NS, _EXTRAS) is True


class TestExtraDirectivesDirection:
    """Only GROWTH is weakening: shrinking leaves the standing directive."""

    @pytest.mark.unit
    def test_adding_a_directive_is_weakening(self) -> None:
        assert is_weakening(_NS, _EXTRAS, current="[]", new='[{"id": "a"}]') is True

    @pytest.mark.unit
    def test_first_write_of_a_directive_is_weakening(self) -> None:
        assert is_weakening(_NS, _EXTRAS, current=None, new='[{"id": "a"}]') is True

    @pytest.mark.unit
    def test_removing_a_directive_is_not_weakening(self) -> None:
        assert is_weakening(_NS, _EXTRAS, current='[{"id": "a"}]', new="[]") is False

    @pytest.mark.unit
    def test_editing_in_place_is_not_weakening(self) -> None:
        # Same count, different text: routine wording edits stay quiet, which
        # is what keeps the guardrail worth reading when it does fire.
        assert (
            is_weakening(_NS, _EXTRAS, current='[{"id": "a"}]', new='[{"id": "b"}]')
            is False
        )

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["not json", '{"id": "a"}'])
    def test_an_unparseable_payload_is_not_weakening(self, raw: str) -> None:
        # The type validator rejects it downstream, so judging it here would
        # only turn a malformed write into a confirmation prompt.
        assert is_weakening(_NS, _EXTRAS, current="[]", new=raw) is False


class TestWeakeningDirection:
    @pytest.mark.unit
    @pytest.mark.parametrize("key", _ASK_KEYS)
    def test_disabling_is_weakening(self, key: str) -> None:
        assert is_weakening(_NS, key, current="true", new="false") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("key", _ASK_KEYS)
    def test_first_write_disabling_is_weakening(self, key: str) -> None:
        # Unset resolves to the registered default, which is now on, so the
        # first explicit "false" is the transition worth confirming.
        assert is_weakening(_NS, key, current=None, new="false") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("key", _ASK_KEYS)
    def test_enabling_is_not_weakening(self, key: str) -> None:
        assert is_weakening(_NS, key, current="false", new="true") is False

    @pytest.mark.unit
    @pytest.mark.parametrize("key", _ASK_KEYS)
    def test_rewriting_off_is_not_weakening(self, key: str) -> None:
        assert is_weakening(_NS, key, current="false", new="false") is False


class TestEnforcement:
    @pytest.mark.unit
    async def test_disabling_without_governance_rejected(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "true"

        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [(_NS, "clarification_enabled", "false")],
                governance=None,
                get_current=_current,
            )

    @pytest.mark.unit
    async def test_disabling_with_governance_allowed(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "true"

        await enforce_security_write_governance(
            [(_NS, "clarification_enabled", "false")],
            governance=SettingsWriteGovernance(
                confirm=True, reason="unattended overnight run", actor="ceo"
            ),
            get_current=_current,
        )

    @pytest.mark.unit
    async def test_enabling_needs_no_governance(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "false"

        await enforce_security_write_governance(
            [(_NS, "ask_policy_enabled", "true")],
            governance=None,
            get_current=_current,
        )
