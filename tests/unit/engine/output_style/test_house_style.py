"""Unit + integration tests for the soft house-style prompt layer."""

from collections.abc import Iterator
from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.engine.output_style.adapter import (
    inject_house_style_context,
    should_inject_house_style,
)
from synthorg.engine.output_style.house_style import build_house_style_section
from synthorg.engine.output_style.models import HouseStyleDirective, ScopeKind
from synthorg.engine.output_style.provider import (
    SnapshotHouseStyleProvider,
    current_house_style_provider,
    set_house_style_provider,
)
from synthorg.engine.prompt import build_system_prompt

_DIRECTIVES = (
    HouseStyleDirective(id="d_all", text="Org-wide: be concise."),
    HouseStyleDirective(
        id="d_eng",
        text="Engineering: terse commit style.",
        scope="Engineering",
        scope_kind=ScopeKind.DEPARTMENT,
    ),
    HouseStyleDirective(
        id="d_legal",
        text="Legal: formal register.",
        scope="Lawyer",
        scope_kind=ScopeKind.ROLE,
    ),
)


def _agent(
    *, role: str = "Developer", department: str = "Engineering"
) -> AgentIdentity:
    return AgentIdentity(
        name="Test Agent",
        role=role,
        department=department,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        personality=PersonalityConfig(description="A precise thinker."),
    )


@pytest.fixture
def _reset_provider() -> Iterator[None]:
    """Restore the ambient provider after a test mutates it."""
    previous = current_house_style_provider()
    try:
        yield
    finally:
        set_house_style_provider(previous)


class TestRendering:
    @pytest.mark.unit
    def test_empty_directives_render_empty(self) -> None:
        assert build_house_style_section(()) == ""

    @pytest.mark.unit
    def test_directives_render_as_bullets(self) -> None:
        body = build_house_style_section(_DIRECTIVES[:1])
        assert body == "- Org-wide: be concise."


class TestScopeFiltering:
    @pytest.mark.unit
    def test_org_wide_reaches_everyone(self) -> None:
        provider = SnapshotHouseStyleProvider(_DIRECTIVES)
        directives = provider.list_directives(role="Analyst", department="Finance")
        assert [d.id for d in directives] == ["d_all"]

    @pytest.mark.unit
    def test_department_scope_applies(self) -> None:
        provider = SnapshotHouseStyleProvider(_DIRECTIVES)
        directives = provider.list_directives(
            role="Developer", department="Engineering"
        )
        assert {d.id for d in directives} == {"d_all", "d_eng"}

    @pytest.mark.unit
    def test_role_scope_applies_case_insensitive(self) -> None:
        provider = SnapshotHouseStyleProvider(_DIRECTIVES)
        directives = provider.list_directives(role="lawyer", department="Legal")
        assert {d.id for d in directives} == {"d_all", "d_legal"}

    @pytest.mark.unit
    def test_disabled_provider_yields_nothing(self) -> None:
        provider = SnapshotHouseStyleProvider(_DIRECTIVES, enabled=False)
        assert (
            provider.list_directives(role="Developer", department="Engineering") == ()
        )


class TestAdapter:
    @pytest.mark.unit
    def test_inject_sets_section_when_directives_present(self) -> None:
        provider = SnapshotHouseStyleProvider(_DIRECTIVES)
        context: dict[str, object] = {}
        inject_house_style_context(context, _agent(), provider=provider)
        assert context["house_style"] is True
        assert "Org-wide" in str(context["house_style_section"])
        assert "Engineering" in str(context["house_style_section"])

    @pytest.mark.unit
    def test_inject_disables_when_no_provider(self) -> None:
        context: dict[str, object] = {}
        inject_house_style_context(context, _agent(), provider=None)
        assert context["house_style"] is False
        assert context["house_style_section"] is None


@pytest.mark.usefixtures("_reset_provider")
class TestIntegration:
    @pytest.mark.unit
    def test_prompt_includes_house_style_section(self) -> None:
        set_house_style_provider(SnapshotHouseStyleProvider(_DIRECTIVES))
        result = build_system_prompt(agent=_agent())
        assert "## House Writing Style" in result.content
        assert "Org-wide: be concise." in result.content
        assert "Engineering: terse commit style." in result.content
        assert "Legal: formal register." not in result.content
        assert "house_style" in result.sections

    @pytest.mark.unit
    def test_prompt_omits_section_without_provider(self) -> None:
        set_house_style_provider(None)
        result = build_system_prompt(agent=_agent())
        assert "## House Writing Style" not in result.content
        assert "house_style" not in result.sections

    @pytest.mark.unit
    def test_should_inject_reflects_scope(self) -> None:
        set_house_style_provider(SnapshotHouseStyleProvider(_DIRECTIVES))
        assert should_inject_house_style(_agent()) is True
        set_house_style_provider(None)
        assert should_inject_house_style(_agent()) is False
