"""Tests for effective-posture resolution and config-knob threading."""

import pytest

from synthorg.config.posture_config import PostureConfig
from synthorg.organization.enums import CompanyType
from synthorg.templates._config_assembly import thread_posture_knobs
from synthorg.templates.enums import PostureName
from synthorg.templates.errors import TemplatePostureError
from synthorg.templates.postures import resolve_template_posture
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
)


def _template(
    *,
    posture: PostureName | None = None,
    extends: str | None = None,
    uses_packs: tuple[str, ...] = (),
) -> CompanyTemplate:
    """Build a minimal template with the given posture / inheritance."""
    return CompanyTemplate(
        metadata=TemplateMetadata(name="t", company_type=CompanyType.CUSTOM),
        agents=(TemplateAgentConfig(role="Developer"),),
        posture=posture,
        extends=extends,
        uses_packs=uses_packs,
    )


def _no_load(name: str) -> CompanyTemplate:
    """Loader stub that should never be called."""
    msg = f"unexpected load of {name!r}"
    raise AssertionError(msg)


@pytest.mark.unit
class TestResolveTemplatePosture:
    def test_no_posture_anywhere_is_none(self) -> None:
        result = resolve_template_posture(
            _template(),
            load_pack=_no_load,
            load_parent=_no_load,
        )
        assert result is None

    def test_host_posture_wins(self) -> None:
        result = resolve_template_posture(
            _template(posture=PostureName.SECURITY_HARDENED),
            load_pack=_no_load,
            load_parent=_no_load,
        )
        assert result is not None
        assert result.name == "security_hardened"
        assert result.red_team is True

    def test_inherits_parent_when_host_has_none(self) -> None:
        parent = _template(posture=PostureName.AUTONOMOUS)
        result = resolve_template_posture(
            _template(extends="parent"),
            load_pack=_no_load,
            load_parent=lambda _name: parent,
        )
        assert result is not None
        assert result.name == "autonomous"

    def test_host_posture_overrides_parent(self) -> None:
        parent = _template(posture=PostureName.AUTONOMOUS)
        result = resolve_template_posture(
            _template(posture=PostureName.COST_DISCIPLINED, extends="parent"),
            load_pack=_no_load,
            load_parent=lambda _name: parent,
        )
        assert result is not None
        assert result.name == "cost_disciplined"
        assert result.auto_downgrade is True
        # Child wins outright: the parent's flags are NOT inherited.
        assert result.steering is False
        assert result.knowledge_substrate is False

    def test_pack_posture_unions_into_host(self) -> None:
        security_pack = _template(posture=PostureName.SECURITY_HARDENED)
        result = resolve_template_posture(
            _template(posture=PostureName.COST_DISCIPLINED, uses_packs=("sec",)),
            load_pack=lambda _name: security_pack,
            load_parent=_no_load,
        )
        assert result is not None
        # Host name kept; pack capability folded in.
        assert result.name == "cost_disciplined"
        assert result.auto_downgrade is True
        assert result.red_team is True
        assert result.red_team_grounding == "knowledge_substrate"

    def test_pack_only_posture_keeps_pack_name(self) -> None:
        security_pack = _template(posture=PostureName.SECURITY_HARDENED)
        result = resolve_template_posture(
            _template(uses_packs=("sec",)),
            load_pack=lambda _name: security_pack,
            load_parent=_no_load,
        )
        assert result is not None
        # With no host/parent posture the pack union is returned verbatim, so
        # its name survives for observability (logged by the seeder).
        assert result.name == "security_hardened"
        assert result.red_team is True

    def test_pack_unions_onto_inherited_parent(self) -> None:
        parent = _template(posture=PostureName.AUTONOMOUS)
        security_pack = _template(posture=PostureName.SECURITY_HARDENED)
        result = resolve_template_posture(
            _template(extends="parent", uses_packs=("sec",)),
            load_pack=lambda _name: security_pack,
            load_parent=lambda _name: parent,
        )
        assert result is not None
        # Host name inherited from parent; pack folds red_team on top.
        assert result.name == "autonomous"
        assert result.steering is True
        assert result.knowledge_substrate is True
        assert result.red_team is True

    def test_multiple_packs_union_their_flags(self) -> None:
        cost_pack = _template(posture=PostureName.COST_DISCIPLINED)
        security_pack = _template(posture=PostureName.SECURITY_HARDENED)
        packs = {"cost": cost_pack, "sec": security_pack}
        result = resolve_template_posture(
            _template(posture=PostureName.AUTONOMOUS, uses_packs=("cost", "sec")),
            load_pack=lambda name: packs[name],
            load_parent=_no_load,
        )
        assert result is not None
        assert result.name == "autonomous"
        assert result.auto_downgrade is True
        assert result.red_team is True

    def test_inheritance_cycle_raises(self) -> None:
        def _self_parent(_name: str) -> CompanyTemplate:
            return _template(extends="loop")

        with pytest.raises(TemplatePostureError, match="max depth"):
            resolve_template_posture(
                _template(extends="loop"),
                load_pack=_no_load,
                load_parent=_self_parent,
            )


@pytest.mark.unit
class TestThreadPostureKnobs:
    def test_threads_red_team_and_budget(self) -> None:
        result: dict[str, object] = {}
        posture = PostureConfig(
            name="security_hardened",
            red_team=True,
            red_team_grounding="knowledge_substrate",
            auto_downgrade=True,
        )
        thread_posture_knobs(result, posture)
        assert result["posture"]["name"] == "security_hardened"  # type: ignore[index]
        assert result["security"] == {
            "red_team": {
                "enabled": True,
                "grounding_checker_kind": "knowledge_substrate",
            },
        }
        assert result["budget"] == {"auto_downgrade": {"enabled": True}}

    def test_no_red_team_no_security_key(self) -> None:
        result: dict[str, object] = {}
        thread_posture_knobs(result, PostureConfig(name="autonomous"))
        assert "security" not in result
        assert "budget" not in result
        assert result["posture"]["name"] == "autonomous"  # type: ignore[index]
