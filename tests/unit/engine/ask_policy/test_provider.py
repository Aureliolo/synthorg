"""Scope filtering and the ambient binding for the ask-policy provider."""

import pytest
from pydantic import ValidationError

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.engine.ask_policy.directives import ASK_DIRECTIVES
from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.ask_policy.provider import (
    AskPolicyProvider,
    SnapshotAskPolicyProvider,
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.engine.strategy.active_principle import ScopeKind

_EXTRAS = (
    AskDirective(id="x_all", text="Always ask before a schema change."),
    AskDirective(
        id="x_eng",
        text="Engineering: ask before breaking a public API.",
        scope="Engineering",
        scope_kind=ScopeKind.DEPARTMENT,
    ),
    AskDirective(
        id="x_legal",
        text="Lawyer: ask before signing anything.",
        scope="Lawyer",
        scope_kind=ScopeKind.ROLE,
    ),
)


class TestExtraScopeFiltering:
    @pytest.mark.unit
    def test_org_wide_reaches_everyone(self) -> None:
        provider = SnapshotAskPolicyProvider(_EXTRAS)
        extras = provider.list_extra_directives(role="Analyst", department="Finance")
        assert [d.id for d in extras] == ["x_all"]

    @pytest.mark.unit
    def test_department_scope_applies(self) -> None:
        provider = SnapshotAskPolicyProvider(_EXTRAS)
        extras = provider.list_extra_directives(
            role="Developer", department="Engineering"
        )
        assert {d.id for d in extras} == {"x_all", "x_eng"}

    @pytest.mark.unit
    def test_role_scope_applies_case_insensitive(self) -> None:
        provider = SnapshotAskPolicyProvider(_EXTRAS)
        extras = provider.list_extra_directives(role="lawyer", department="Legal")
        assert {d.id for d in extras} == {"x_all", "x_legal"}

    @pytest.mark.unit
    def test_disabled_provider_yields_nothing(self) -> None:
        provider = SnapshotAskPolicyProvider(_EXTRAS, enabled=False)
        assert provider.enabled is False
        assert provider.list_extra_directives(role="Developer", department="Eng") == ()


class TestBaseDirective:
    @pytest.mark.unit
    def test_base_directive_is_served_regardless_of_scope(self) -> None:
        provider = SnapshotAskPolicyProvider()
        text = provider.base_directive(autonomy=AutonomyLevel.LOCKED, detail="full")
        assert text == ASK_DIRECTIVES[AutonomyLevel.LOCKED]


class TestAmbientBinding:
    @pytest.mark.unit
    def test_set_and_current_round_trip(self) -> None:
        provider = SnapshotAskPolicyProvider()
        set_ask_policy_provider(provider)
        assert current_ask_policy_provider() is provider
        set_ask_policy_provider(None)
        assert current_ask_policy_provider() is None

    @pytest.mark.unit
    def test_snapshot_satisfies_the_protocol(self) -> None:
        assert isinstance(SnapshotAskPolicyProvider(), AskPolicyProvider)


class TestDirectiveModel:
    @pytest.mark.unit
    def test_role_scope_may_not_use_the_all_sentinel(self) -> None:
        with pytest.raises(ValidationError):
            AskDirective(id="bad", text="x", scope="all", scope_kind=ScopeKind.ROLE)

    @pytest.mark.unit
    def test_all_scope_may_not_name_a_role(self) -> None:
        with pytest.raises(ValidationError):
            AskDirective(id="bad", text="x", scope="Lawyer", scope_kind=ScopeKind.ALL)
