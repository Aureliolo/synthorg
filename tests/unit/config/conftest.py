"""Unit test configuration and fixtures for config models."""

from typing import TYPE_CHECKING, Protocol

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.api.config import ApiConfig
from synthorg.backup.config import BackupConfig
from synthorg.budget.config import BudgetConfig
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.cost_tiers import CostTiersConfig
from synthorg.budget.quota import DegradationConfig, SubscriptionConfig
from synthorg.communication.config import CommunicationConfig
from synthorg.config.agent_schema import (
    AgentConfig,
    RoutingConfig,
    RoutingRuleConfig,
    TaskAssignmentConfig,
)
from synthorg.config.posture_config import PostureConfig
from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
)
from synthorg.core.company import CompanyConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.evolution.config import EvolutionConfig
from synthorg.engine.stagnation.models import StagnationDetectionConfig
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.workflow.config import WorkflowConfig
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.org.config import OrgMemoryConfig
from synthorg.persistence.config import PersistenceConfig
from synthorg.security.config import SecurityConfig
from synthorg.security.trust.config import TrustConfig
from synthorg.tools.disclosure_config import ToolDisclosureConfig
from synthorg.tools.mcp.config import MCPConfig
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
from synthorg.workers.config import QueueConfig

if TYPE_CHECKING:
    from pathlib import Path


class ConfigFileFactory(Protocol):
    """Callable signature for the tmp_config_file fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


# ── Factories ──────────────────────────────────────────────────────


class ProviderModelConfigFactory(ModelFactory[ProviderModelConfig]):
    __model__ = ProviderModelConfig
    # Pin the optional staleness record to ``None``: polyfactory otherwise
    # builds a random ``ModelStaleness`` whose ``last_seen`` can land after
    # ``flagged_at``, tripping the cross-field invariant. The invariant has
    # dedicated coverage in ``tests/unit/config/test_model_staleness.py``.
    stale = None


class ProviderConfigFactory(ModelFactory[ProviderConfig]):
    __model__ = ProviderConfig
    auth_type = "api_key"
    # API-key auth resolves its credential from the catalog, so a
    # connection_name is mandatory; pin it deterministically.
    connection_name = "provider-factory"
    models = ()
    retry = RetryConfig()
    rate_limiter = RateLimiterConfig()
    subscription = SubscriptionConfig()
    degradation = DegradationConfig()


class RoutingRuleConfigFactory(ModelFactory[RoutingRuleConfig]):
    __model__ = RoutingRuleConfig
    task_type = "test"


class RoutingConfigFactory(ModelFactory[RoutingConfig]):
    __model__ = RoutingConfig
    rules = ()
    fallback_chain = ()


class AgentConfigFactory(ModelFactory[AgentConfig]):
    __model__ = AgentConfig


class RootConfigFactory(ModelFactory[RootConfig]):
    __model__ = RootConfig
    departments = ()
    agents = ()
    custom_roles = ()
    workflow_handoffs = ()
    escalation_paths = ()
    providers: dict[str, ProviderConfig] = {}  # noqa: RUF012
    config = CompanyConfig()
    api = ApiConfig()
    budget = BudgetConfig()
    communication = CommunicationConfig()
    routing = RoutingConfig()
    logging = None
    coordination_metrics = CoordinationMetricsConfig()
    task_assignment = TaskAssignmentConfig()
    memory = CompanyMemoryConfig()
    persistence = PersistenceConfig()
    cost_tiers = CostTiersConfig()
    org_memory = OrgMemoryConfig()
    sandboxing = SandboxingConfig()
    mcp = MCPConfig()
    security = SecurityConfig()
    trust = TrustConfig()
    promotion = PromotionConfig()
    performance = PerformanceConfig()
    # Pinned to the valid default: polyfactory would otherwise synthesise an
    # EvolutionConfig whose nested ShadowEvaluationConfig fails its cross-field
    # validator.
    evolution = EvolutionConfig()
    # Pinned to the valid default: polyfactory generates the nested
    # StagnationConfig's window_size and min_tool_turns independently and
    # intermittently produces min_tool_turns > window_size, which the model's
    # cross-field validator rejects.
    stagnation = StagnationDetectionConfig()
    coordination = CoordinationSectionConfig()
    strategy = StrategyConfig()
    backup = BackupConfig()
    workflow = WorkflowConfig()
    tool_disclosure = ToolDisclosureConfig()
    design_tools = None
    communication_tools = None
    analytics_tools = None
    # Fixed to the default (disabled) because the cross-model
    # validator on RootConfig rejects queue.enabled=True with an
    # internal bus, and polyfactory cannot coordinate the two
    # sub-configs automatically.
    queue = QueueConfig()
    # Pinned to the valid default: PostureConfig's cross-field validator
    # requires red_team=True whenever red_team_grounding is non-heuristic, and
    # polyfactory draws the two fields independently, intermittently producing
    # the rejected combination.
    posture = PostureConfig()


# ── Sample YAML strings ──────────────────────────────────────────

MINIMAL_VALID_YAML = "company_name: Test Corp\n"

FULL_VALID_YAML = """\
company_name: Test Corp
company_type: startup
departments:
  - name: Engineering
    head: cto
    budget_percent: 60.0
agents:
  - name: Alice
    role: Backend Developer
    department: Engineering
    level: senior
budget:
  total_monthly: 500.0
  alerts:
    warn_at: 75
    critical_at: 90
    hard_stop_at: 100
providers:
  example-provider:
    connection_name: provider-example
    models:
      - id: test-model-001
        alias: medium
routing:
  strategy: cost_aware
  fallback_chain:
    - medium
"""

INVALID_SYNTAX_YAML = """\
company_name: Test Corp
invalid: [unterminated
"""

MISSING_REQUIRED_YAML = """\
company_name: ""
"""

INVALID_FIELD_VALUES_YAML = """\
company_name: Test Corp
budget:
  total_monthly: -100.0
"""

ENV_VAR_SIMPLE_YAML = """\
company_name: ${COMPANY_NAME}
"""

ENV_VAR_NESTED_YAML = """\
company_name: ${COMPANY_NAME}
budget:
  total_monthly: 500.0
  alerts:
    warn_at: 75
    critical_at: 90
    hard_stop_at: 100
providers:
  example-provider:
    connection_name: provider-example
    base_url: ${EXAMPLE_PROVIDER_BASE_URL:-https://api.example.com}
"""

ENV_VAR_MISSING_YAML = """\
company_name: ${UNDEFINED_VAR}
"""

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> ConfigFileFactory:
    def _create(content: str, name: str = "config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _create
