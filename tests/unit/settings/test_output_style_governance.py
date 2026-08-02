"""Unit tests for output-style security-write governance."""

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)
from synthorg.settings.write_governance_policy import is_guarded, is_weakening

_NS = "output_style"


class TestWeakeningDirection:
    @pytest.mark.unit
    def test_disabling_is_weakening(self) -> None:
        assert is_weakening(_NS, "enabled", current="true", new="false") is True

    @pytest.mark.unit
    def test_first_write_disabling_is_weakening(self) -> None:
        assert is_weakening(_NS, "enabled", current=None, new="false") is True

    @pytest.mark.unit
    def test_enabling_is_not_weakening(self) -> None:
        assert is_weakening(_NS, "enabled", current="false", new="true") is False

    @pytest.mark.unit
    def test_enabling_shadow_is_weakening(self) -> None:
        assert is_weakening(_NS, "shadow_mode", current="false", new="true") is True

    @pytest.mark.unit
    def test_leaving_shadow_is_not_weakening(self) -> None:
        assert is_weakening(_NS, "shadow_mode", current="true", new="false") is False

    @pytest.mark.unit
    def test_adding_exemption_is_weakening(self) -> None:
        current = "[]"
        new = '[{"rule_id": "emdash_literal", "scope_kind": "path", "match": "src/**", "reason": "filter"}]'  # noqa: E501
        assert is_weakening(_NS, "exemptions", current=current, new=new) is True

    @pytest.mark.unit
    def test_removing_exemption_is_not_weakening(self) -> None:
        current = '[{"rule_id": "emdash_literal", "scope_kind": "path", "match": "src/**", "reason": "filter"}]'  # noqa: E501
        new = "[]"
        assert is_weakening(_NS, "exemptions", current=current, new=new) is False

    @pytest.mark.unit
    def test_pack_is_guarded(self) -> None:
        # A pack swap can replace the whole rule set, so it routes through the
        # same confirm+reason+actor guardrail as disabling the policy.
        assert is_guarded(_NS, "pack") is True

    @pytest.mark.unit
    def test_changing_pack_is_weakening(self) -> None:
        assert is_weakening(_NS, "pack", current="default", new="permissive") is True

    @pytest.mark.unit
    def test_unchanged_pack_is_not_weakening(self) -> None:
        assert is_weakening(_NS, "pack", current="default", new="default") is False


class TestEnforcement:
    @pytest.mark.unit
    async def test_weakening_without_governance_rejected(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "true"

        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [(_NS, "enabled", "false")],
                governance=None,
                get_current=_current,
            )

    @pytest.mark.unit
    async def test_weakening_with_governance_allowed(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "true"

        await enforce_security_write_governance(
            [(_NS, "enabled", "false")],
            governance=SettingsWriteGovernance(
                confirm=True, reason="maintenance window", actor="ceo"
            ),
            get_current=_current,
        )

    @pytest.mark.unit
    async def test_tightening_needs_no_governance(self) -> None:
        async def _current(_ns: str, _key: str) -> str | None:
            return "false"

        await enforce_security_write_governance(
            [(_NS, "enabled", "true")],
            governance=None,
            get_current=_current,
        )
