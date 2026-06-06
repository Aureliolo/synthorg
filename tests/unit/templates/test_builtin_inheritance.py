"""Tests for builtin template inheritance chains.

Verifies that the inheritance tree produces correct expanded configs:
- solo_founder -> startup -> dev_shop, product_team
- research_lab -> data_team
- agency, full_company, consultancy remain standalone
"""

import pytest

from synthorg.config.schema import RootConfig
from synthorg.templates.loader import load_template
from synthorg.templates.renderer import render_template


def _render(name: str) -> RootConfig:
    """Load and render a builtin template by name."""
    loaded = load_template(name)
    return render_template(loaded)


def _roles(config: RootConfig) -> list[str]:
    """Extract sorted role list from rendered config."""
    return sorted(a.role for a in config.agents)


def _dept_names(config: RootConfig) -> set[str]:
    """Extract department names from rendered config."""
    return {d.name for d in config.departments}


def _dept_budget(config: RootConfig, name: str) -> float:
    """Get budget_percent for a department by name."""
    for d in config.departments:
        if d.name == name:
            return d.budget_percent
    msg = f"Department {name!r} not found"
    raise ValueError(msg)


# ── solo_founder -> startup ─────────────────────────────────────


@pytest.mark.unit
class TestStartupExtendsSoloFounder:
    def test_agent_count(self) -> None:
        config = _render("startup")
        assert len(config.agents) == 5

    def test_agent_roles(self) -> None:
        config = _render("startup")
        roles = _roles(config)
        assert roles == [
            "CEO",
            "CTO",
            "Full-Stack Developer",
            "Full-Stack Developer",
            "Product Manager",
        ]

    def test_ceo_inherited_from_solo_founder(self) -> None:
        """CEO is inherited unchanged from solo_founder."""
        config = _render("startup")
        ceo = next(a for a in config.agents if a.role == "CEO")
        assert ceo.level.value == "c_suite"

    def test_fullstack_senior_overridden(self) -> None:
        """Full-Stack Developer (senior) has pragmatic_builder traits."""
        config = _render("startup")
        fs_agents = [a for a in config.agents if a.role == "Full-Stack Developer"]
        senior = next(a for a in fs_agents if a.level.value == "senior")
        assert senior.personality
        # pragmatic_builder has "practical" in traits
        traits = senior.personality.get("traits", ())
        assert isinstance(traits, (list, tuple))
        assert "practical" in traits

    def test_departments(self) -> None:
        config = _render("startup")
        assert _dept_names(config) == {"executive", "engineering", "product"}

    def test_company_config(self) -> None:
        config = _render("startup")
        assert config.config.autonomy.level.value == "semi"
        assert config.config.communication_pattern == "hybrid"

    def test_company_type(self) -> None:
        config = _render("startup")
        assert config.company_type.value == "startup"


# ── solo_founder -> startup -> dev_shop ─────────────────────────


@pytest.mark.unit
class TestDevShopExtendsStartup:
    def test_agent_count(self) -> None:
        config = _render("dev_shop")
        assert len(config.agents) == 8

    def test_agent_roles(self) -> None:
        config = _render("dev_shop")
        roles = _roles(config)
        assert roles == [
            "Backend Developer",
            "Backend Developer",
            "Backend Developer",
            "DevOps/SRE Engineer",
            "Frontend Developer",
            "QA Engineer",
            "QA Lead",
            "Software Architect",
        ]

    def test_no_inherited_executive_agents(self) -> None:
        """CEO and CTO from startup chain are removed."""
        config = _render("dev_shop")
        roles = {a.role for a in config.agents}
        assert "CEO" not in roles
        assert "CTO" not in roles
        assert "Product Manager" not in roles

    def test_departments(self) -> None:
        config = _render("dev_shop")
        names = _dept_names(config)
        assert names == {"engineering", "quality_assurance", "operations"}
        assert "executive" not in names
        assert "product" not in names

    def test_engineering_budget(self) -> None:
        config = _render("dev_shop")
        assert _dept_budget(config, "engineering") == 70

    def test_company_config(self) -> None:
        config = _render("dev_shop")
        assert config.config.autonomy.level.value == "semi"
        assert config.config.communication_pattern == "hybrid"

    def test_company_type(self) -> None:
        config = _render("dev_shop")
        assert config.company_type.value == "dev_shop"


# ── solo_founder -> startup -> product_team ─────────────────────


@pytest.mark.unit
class TestProductTeamExtendsStartup:
    def test_agent_count(self) -> None:
        config = _render("product_team")
        assert len(config.agents) == 10

    def test_agent_roles(self) -> None:
        config = _render("product_team")
        roles = _roles(config)
        assert roles == [
            "Automation Engineer",
            "Backend Developer",
            "Backend Developer",
            "Data Analyst",
            "Frontend Developer",
            "Full-Stack Developer",
            "Product Manager",
            "QA Engineer",
            "UX Designer",
            "UX Researcher",
        ]

    def test_product_manager_inherited(self) -> None:
        """Product Manager is inherited from startup chain."""
        config = _render("product_team")
        pm = next(a for a in config.agents if a.role == "Product Manager")
        assert pm.level.value == "senior"

    def test_no_executive_agents(self) -> None:
        """CEO and CTO from startup chain are removed."""
        config = _render("product_team")
        roles = {a.role for a in config.agents}
        assert "CEO" not in roles
        assert "CTO" not in roles

    def test_departments(self) -> None:
        config = _render("product_team")
        names = _dept_names(config)
        assert names == {
            "engineering",
            "product",
            "design",
            "quality_assurance",
            "data_analytics",
        }
        assert "executive" not in names

    def test_communication_overridden(self) -> None:
        config = _render("product_team")
        assert config.config.communication_pattern == "meeting_based"

    def test_company_type(self) -> None:
        config = _render("product_team")
        assert config.company_type.value == "product_team"


# ── research_lab -> data_team ───────────────────────────────────


@pytest.mark.unit
class TestDataTeamExtendsResearchLab:
    def test_agent_count(self) -> None:
        config = _render("data_team")
        assert len(config.agents) == 6

    def test_agent_roles(self) -> None:
        config = _render("data_team")
        roles = _roles(config)
        assert roles == [
            "Backend Developer",
            "Data Analyst",
            "Data Analyst",
            "Data Engineer",
            "ML Engineer",
            "Technical Writer",
        ]

    def test_data_agents_inherited(self) -> None:
        """Data Engineer, 2x Data Analyst, ML Engineer inherited."""
        config = _render("data_team")
        data_roles = [a.role for a in config.agents if a.department == "data_analytics"]
        assert sorted(data_roles) == [
            "Data Analyst",
            "Data Analyst",
            "Data Engineer",
            "ML Engineer",
        ]

    def test_software_architect_removed(self) -> None:
        """Software Architect from research_lab is removed."""
        config = _render("data_team")
        roles = {a.role for a in config.agents}
        assert "Software Architect" not in roles

    def test_departments(self) -> None:
        config = _render("data_team")
        names = _dept_names(config)
        assert names == {"data_analytics", "engineering"}
        assert "product" not in names

    def test_data_analytics_budget(self) -> None:
        config = _render("data_team")
        assert _dept_budget(config, "data_analytics") == 65

    def test_company_config(self) -> None:
        config = _render("data_team")
        assert config.config.autonomy.level.value == "full"
        assert config.config.communication_pattern == "event_driven"

    def test_company_type(self) -> None:
        config = _render("data_team")
        assert config.company_type.value == "data_team"


# ── Standalone templates unchanged ──────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("template_name", "expected_count"),
    [
        ("agency", 12),
        ("consultancy", 5),
        ("solo_founder", 2),
        ("research_lab", 7),
    ],
)
class TestStandaloneTemplatesUnchanged:
    def test_renders_to_valid_config(
        self,
        template_name: str,
        expected_count: int,
    ) -> None:
        config = _render(template_name)
        assert isinstance(config, RootConfig)
        assert len(config.agents) == expected_count


@pytest.mark.unit
class TestFullCompanyStandalone:
    def test_renders_to_valid_config(self) -> None:
        config = _render("full_company")
        assert isinstance(config, RootConfig)
        assert len(config.agents) >= 8
        assert len(config.departments) >= 3


# ── Three-level inheritance regression ───────────────────────────


@pytest.mark.unit
class TestThreeLevelInheritanceRegression:
    """Regression tests for solo_founder -> startup -> dev_shop/product_team.

    Verifies that workflow config and agent merge_id handling work
    correctly across three inheritance levels.
    """

    def test_dev_shop_workflow_from_own_config(self) -> None:
        """dev_shop uses its own workflow_config, not grandparent's.

        Each template's workflow_config is rendered independently by the
        renderer (not inherited via merge), so dev_shop gets its own
        kanban WIP limit of 2, not solo_founder's 3.
        """
        config = _render("dev_shop")
        wf = config.workflow
        assert wf is not None
        kanban = wf.kanban
        in_progress = next(
            w for w in kanban.wip_limits if w.column.value == "in_progress"
        )
        assert in_progress.limit == 2

    def test_dev_shop_grandparent_agents_removed(self) -> None:
        """Agents originating from solo_founder (CEO) are removable by dev_shop.

        startup inherits CEO from solo_founder. After the solo_founder +
        startup merge, merge_ids are stripped. dev_shop removes CEO by
        (role, department, '') key -- no merge_id needed.
        """
        config = _render("dev_shop")
        roles = {a.role for a in config.agents}
        assert "CEO" not in roles

    def test_product_team_override_survives_chain(self) -> None:
        """product_team overrides a Full-Stack Developer that was already
        overridden by startup from solo_founder's original.

        Chain: solo_founder defines FS Dev -> startup overrides via
        merge_id -> merge strips merge_id -> product_team overrides
        by (role, dept, '') key -> personality is communication_bridge.
        """
        config = _render("product_team")
        fs_agents = [a for a in config.agents if a.role == "Full-Stack Developer"]
        assert len(fs_agents) == 1
        assert fs_agents[0].personality is not None
        # communication_bridge preset has articulate, sociable, diplomatic
        traits = fs_agents[0].personality.get("traits", ())
        assert isinstance(traits, (list, tuple))
        assert "articulate" in traits
