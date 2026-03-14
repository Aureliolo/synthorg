"""Tests for config schema models."""

import pytest
from pydantic import ValidationError

from synthorg.config.schema import (
    AgentConfig,
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
    RoutingConfig,
    RoutingRuleConfig,
    TaskAssignmentConfig,
)
from synthorg.core.enums import CompanyType, SeniorityLevel

from .conftest import (
    AgentConfigFactory,
    ProviderConfigFactory,
    ProviderModelConfigFactory,
    RootConfigFactory,
    RoutingConfigFactory,
    RoutingRuleConfigFactory,
)

# ── ProviderModelConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestProviderModelConfig:
    def test_valid_minimal(self) -> None:
        m = ProviderModelConfig(id="test-model:8b")
        assert m.id == "test-model:8b"
        assert m.alias is None
        assert m.cost_per_1k_input == 0.0
        assert m.cost_per_1k_output == 0.0
        assert m.max_context == 200_000

    def test_valid_full(self) -> None:
        m = ProviderModelConfig(
            id="test-model:8b",
            alias="medium",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_context=200_000,
        )
        assert m.alias == "medium"
        assert m.cost_per_1k_input == 0.003

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProviderModelConfig(id="")

    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProviderModelConfig(id="   ")

    def test_whitespace_alias_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            ProviderModelConfig(id="m1", alias="   ")

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProviderModelConfig(id="m1", cost_per_1k_input=-1.0)

    def test_frozen(self) -> None:
        m = ProviderModelConfig(id="m1")
        with pytest.raises(ValidationError):
            m.id = "m2"  # type: ignore[misc]

    def test_estimated_latency_ms_default_none(self) -> None:
        m = ProviderModelConfig(id="test-model")
        assert m.estimated_latency_ms is None

    def test_estimated_latency_ms_valid(self) -> None:
        m = ProviderModelConfig(id="test-model", estimated_latency_ms=200)
        assert m.estimated_latency_ms == 200

    def test_estimated_latency_ms_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            ProviderModelConfig(id="test-model", estimated_latency_ms=0)

    def test_estimated_latency_ms_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            ProviderModelConfig(id="test-model", estimated_latency_ms=-100)

    def test_estimated_latency_ms_exceeds_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal"):
            ProviderModelConfig(id="test-model", estimated_latency_ms=300_001)

    def test_factory(self) -> None:
        m = ProviderModelConfigFactory.build()
        assert isinstance(m, ProviderModelConfig)
        assert m.id


# ── ProviderConfig ───────────────────────────────────────────────


@pytest.mark.unit
class TestProviderConfig:
    def test_defaults(self) -> None:
        p = ProviderConfig()
        assert p.api_key is None
        assert p.base_url is None
        assert p.models == ()

    def test_with_models(self) -> None:
        p = ProviderConfig(
            models=(
                ProviderModelConfig(id="m1", alias="fast"),
                ProviderModelConfig(id="m2", alias="smart"),
            ),
        )
        assert len(p.models) == 2

    def test_duplicate_model_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate model IDs"):
            ProviderConfig(
                models=(
                    ProviderModelConfig(id="m1"),
                    ProviderModelConfig(id="m1"),
                ),
            )

    def test_duplicate_aliases_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate model aliases"):
            ProviderConfig(
                models=(
                    ProviderModelConfig(id="m1", alias="fast"),
                    ProviderModelConfig(id="m2", alias="fast"),
                ),
            )

    def test_whitespace_api_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            ProviderConfig(api_key="   ")

    def test_whitespace_base_url_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            ProviderConfig(base_url="   ")

    def test_api_key_hidden_from_repr(self) -> None:
        p = ProviderConfig(api_key="sk-secret-key-123")
        assert "sk-secret-key-123" not in repr(p)

    def test_factory(self) -> None:
        p = ProviderConfigFactory.build()
        assert isinstance(p, ProviderConfig)


# ── RoutingRuleConfig ────────────────────────────────────────────


@pytest.mark.unit
class TestRoutingRuleConfig:
    def test_minimal_with_task_type(self) -> None:
        r = RoutingRuleConfig(preferred_model="medium", task_type="dev")
        assert r.preferred_model == "medium"
        assert r.role_level is None
        assert r.task_type == "dev"
        assert r.fallback is None

    def test_minimal_with_role_level(self) -> None:
        r = RoutingRuleConfig(
            preferred_model="medium",
            role_level=SeniorityLevel.SENIOR,
        )
        assert r.role_level == SeniorityLevel.SENIOR
        assert r.task_type is None

    def test_no_matcher_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least"):
            RoutingRuleConfig(preferred_model="medium")

    def test_full(self) -> None:
        r = RoutingRuleConfig(
            role_level=SeniorityLevel.SENIOR,
            task_type="development",
            preferred_model="large",
            fallback="medium",
        )
        assert r.role_level == SeniorityLevel.SENIOR
        assert r.task_type == "development"

    def test_blank_preferred_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingRuleConfig(preferred_model="")

    def test_whitespace_task_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            RoutingRuleConfig(preferred_model="medium", task_type="   ")

    def test_whitespace_fallback_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            RoutingRuleConfig(preferred_model="medium", fallback="   ")

    def test_factory(self) -> None:
        r = RoutingRuleConfigFactory.build()
        assert isinstance(r, RoutingRuleConfig)


# ── RoutingConfig ────────────────────────────────────────────────


@pytest.mark.unit
class TestRoutingConfig:
    def test_defaults(self) -> None:
        r = RoutingConfig()
        assert r.strategy == "cost_aware"
        assert r.rules == ()
        assert r.fallback_chain == ()

    def test_with_rules(self) -> None:
        r = RoutingConfig(
            rules=(
                RoutingRuleConfig(
                    preferred_model="medium",
                    task_type="dev",
                ),
            ),
            fallback_chain=("medium",),
        )
        assert len(r.rules) == 1
        assert r.fallback_chain == ("medium",)

    def test_whitespace_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            RoutingConfig(strategy="   ")

    def test_whitespace_fallback_entry_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            RoutingConfig(fallback_chain=("   ",))

    def test_factory(self) -> None:
        r = RoutingConfigFactory.build()
        assert isinstance(r, RoutingConfig)


# ── AgentConfig ──────────────────────────────────────────────────


@pytest.mark.unit
class TestAgentConfig:
    def test_minimal(self) -> None:
        a = AgentConfig(
            name="Alice",
            role="Backend Developer",
            department="Engineering",
        )
        assert a.name == "Alice"
        assert a.level == SeniorityLevel.MID
        assert a.personality == {}
        assert a.model == {}

    def test_full(self) -> None:
        a = AgentConfig(
            name="Alice",
            role="Backend Developer",
            department="Engineering",
            level=SeniorityLevel.SENIOR,
            personality={"traits": ["analytical"]},
            model={"provider": "example-provider", "model_id": "test-model:8b"},
        )
        assert a.level == SeniorityLevel.SENIOR
        assert a.personality == {"traits": ["analytical"]}

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentConfig(name="", role="dev", department="eng")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            AgentConfig(name="   ", role="dev", department="eng")

    def test_frozen(self) -> None:
        a = AgentConfig(name="A", role="R", department="D")
        with pytest.raises(ValidationError):
            a.name = "B"  # type: ignore[misc]

    def test_factory(self) -> None:
        a = AgentConfigFactory.build()
        assert isinstance(a, AgentConfig)
        assert a.name


# ── RootConfig ───────────────────────────────────────────────────


@pytest.mark.unit
class TestRootConfig:
    def test_minimal(self) -> None:
        cfg = RootConfig(company_name="Test Corp")
        assert cfg.company_name == "Test Corp"
        assert cfg.company_type == CompanyType.CUSTOM
        assert cfg.departments == ()
        assert cfg.agents == ()
        assert cfg.providers == {}
        assert cfg.logging is None

    def test_full(self) -> None:
        model = ProviderModelConfig(id="m1", alias="fast")
        cfg = RootConfig(
            company_name="Acme AI",
            company_type=CompanyType.STARTUP,
            agents=(
                AgentConfig(
                    name="Alice",
                    role="dev",
                    department="eng",
                ),
            ),
            providers={
                "example-provider": ProviderConfig(models=(model,)),
            },
            routing=RoutingConfig(
                fallback_chain=("fast",),
            ),
        )
        assert cfg.company_type == CompanyType.STARTUP
        assert len(cfg.agents) == 1
        assert "example-provider" in cfg.providers

    def test_defaults_applied(self) -> None:
        cfg = RootConfig(company_name="X")
        assert cfg.budget.total_monthly == 100.0
        assert cfg.communication.default_pattern.value == "hybrid"
        assert cfg.routing.strategy == "cost_aware"

    def test_persistence_defaults(self) -> None:
        cfg = RootConfig(company_name="X")
        assert cfg.persistence.backend == "sqlite"
        assert cfg.persistence.sqlite.path == "synthorg.db"
        assert cfg.persistence.sqlite.wal_mode is True

    def test_persistence_custom_path(self) -> None:
        from synthorg.persistence.config import PersistenceConfig, SQLiteConfig

        cfg = RootConfig(
            company_name="X",
            persistence=PersistenceConfig(
                sqlite=SQLiteConfig(path="data/company-a.db"),
            ),
        )
        assert cfg.persistence.sqlite.path == "data/company-a.db"

    def test_missing_company_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RootConfig()  # type: ignore[call-arg]

    def test_blank_company_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RootConfig(company_name="")

    def test_whitespace_company_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            RootConfig(company_name="   ")

    def test_frozen(self) -> None:
        cfg = RootConfig(company_name="X")
        with pytest.raises(ValidationError):
            cfg.company_name = "Y"  # type: ignore[misc]

    def test_unique_agent_names(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate agent names"):
            RootConfig(
                company_name="X",
                agents=(
                    AgentConfig(name="Alice", role="a", department="a"),
                    AgentConfig(name="Alice", role="b", department="b"),
                ),
            )

    def test_unique_department_names(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Duplicate department names",
        ):
            RootConfig(
                company_name="X",
                departments=(  # type: ignore[arg-type]
                    {"name": "Engineering", "head": "cto"},
                    {"name": "Engineering", "head": "vp"},
                ),
            )

    def test_workflow_handoffs_accepted(self) -> None:
        cfg = RootConfig(
            company_name="X",
            workflow_handoffs=(  # type: ignore[arg-type]
                {
                    "from_department": "eng",
                    "to_department": "qa",
                    "trigger": "code complete",
                },
            ),
        )
        assert len(cfg.workflow_handoffs) == 1

    def test_escalation_paths_accepted(self) -> None:
        cfg = RootConfig(
            company_name="X",
            escalation_paths=(  # type: ignore[arg-type]
                {
                    "from_department": "eng",
                    "to_department": "security",
                    "condition": "vulnerability found",
                },
            ),
        )
        assert len(cfg.escalation_paths) == 1

    def test_routing_references_unknown_model(self) -> None:
        with pytest.raises(
            ValidationError,
            match="unknown model",
        ):
            RootConfig(
                company_name="X",
                routing=RoutingConfig(
                    rules=(
                        RoutingRuleConfig(
                            preferred_model="nonexistent",
                            task_type="dev",
                        ),
                    ),
                ),
            )

    def test_routing_rule_unknown_fallback_rejected(self) -> None:
        model = ProviderModelConfig(id="m1", alias="fast")
        with pytest.raises(ValidationError, match="unknown fallback"):
            RootConfig(
                company_name="X",
                providers={"p": ProviderConfig(models=(model,))},
                routing=RoutingConfig(
                    rules=(
                        RoutingRuleConfig(
                            preferred_model="fast",
                            fallback="nonexistent",
                            task_type="dev",
                        ),
                    ),
                ),
            )

    def test_fallback_chain_unknown_model_rejected(self) -> None:
        model = ProviderModelConfig(id="m1")
        with pytest.raises(
            ValidationError,
            match="fallback_chain references unknown",
        ):
            RootConfig(
                company_name="X",
                providers={"p": ProviderConfig(models=(model,))},
                routing=RoutingConfig(fallback_chain=("nonexistent",)),
            )

    def test_routing_ambiguous_model_ref_across_providers(self) -> None:
        model_a = ProviderModelConfig(id="shared-model")
        model_b = ProviderModelConfig(id="shared-model")
        with pytest.raises(ValidationError, match="Ambiguous model reference"):
            RootConfig(
                company_name="X",
                providers={
                    "provider_a": ProviderConfig(models=(model_a,)),
                    "provider_b": ProviderConfig(models=(model_b,)),
                },
                routing=RoutingConfig(
                    fallback_chain=("shared-model",),
                ),
            )

    def test_routing_ambiguous_alias_across_providers(self) -> None:
        model_a = ProviderModelConfig(id="m1", alias="fast")
        model_b = ProviderModelConfig(id="m2", alias="fast")
        with pytest.raises(ValidationError, match="Ambiguous model reference"):
            RootConfig(
                company_name="X",
                providers={
                    "provider_a": ProviderConfig(models=(model_a,)),
                    "provider_b": ProviderConfig(models=(model_b,)),
                },
                routing=RoutingConfig(
                    rules=(
                        RoutingRuleConfig(
                            preferred_model="fast",
                            task_type="dev",
                        ),
                    ),
                ),
            )

    def test_routing_references_valid_model(self) -> None:
        model = ProviderModelConfig(id="m1", alias="fast")
        cfg = RootConfig(
            company_name="X",
            providers={"p": ProviderConfig(models=(model,))},
            routing=RoutingConfig(
                rules=(
                    RoutingRuleConfig(
                        preferred_model="fast",
                        task_type="dev",
                    ),
                ),
                fallback_chain=("m1",),
            ),
        )
        assert cfg.routing.rules[0].preferred_model == "fast"

    def test_factory(self) -> None:
        cfg = RootConfigFactory.build()
        assert isinstance(cfg, RootConfig)
        assert cfg.company_name


# ── TaskAssignmentConfig ────────────────────────────────────────


@pytest.mark.unit
class TestTaskAssignmentConfig:
    def test_defaults(self) -> None:
        cfg = TaskAssignmentConfig()
        assert cfg.strategy == "role_based"
        assert cfg.min_score == 0.1
        assert cfg.max_concurrent_tasks_per_agent == 5

    def test_frozen(self) -> None:
        cfg = TaskAssignmentConfig()
        with pytest.raises(ValidationError):
            cfg.strategy = "manual"  # type: ignore[misc]

    def test_min_score_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_score"):
            TaskAssignmentConfig(min_score=-0.1)

    def test_min_score_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_score"):
            TaskAssignmentConfig(min_score=1.5)

    def test_min_score_boundaries(self) -> None:
        low = TaskAssignmentConfig(min_score=0.0)
        high = TaskAssignmentConfig(min_score=1.0)
        assert low.min_score == 0.0
        assert high.min_score == 1.0

    def test_max_concurrent_below_one_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="max_concurrent_tasks_per_agent",
        ):
            TaskAssignmentConfig(max_concurrent_tasks_per_agent=0)

    def test_max_concurrent_above_fifty_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="max_concurrent_tasks_per_agent",
        ):
            TaskAssignmentConfig(max_concurrent_tasks_per_agent=51)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskAssignmentConfig(min_score=float("nan"))

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskAssignmentConfig(min_score=float("inf"))

    @pytest.mark.parametrize(
        "name",
        [
            "manual",
            "role_based",
            "load_balanced",
            "cost_optimized",
            "hierarchical",
            "auction",
        ],
    )
    def test_valid_strategy_names_accepted(self, name: str) -> None:
        cfg = TaskAssignmentConfig(strategy=name)
        assert cfg.strategy == name

    def test_unknown_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown assignment strategy"):
            TaskAssignmentConfig(strategy="typo_strategy")

    def test_empty_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskAssignmentConfig(strategy="   ")
