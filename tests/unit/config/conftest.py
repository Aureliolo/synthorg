"""Unit test configuration and fixtures for config models."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
from polyfactory.factories.pydantic_factory import ModelFactory

from ai_company.budget.config import BudgetConfig
from ai_company.communication.config import CommunicationConfig
from ai_company.config.schema import (
    AgentConfig,
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from ai_company.core.company import CompanyConfig

# ── Factories ──────────────────────────────────────────────────────


class ProviderModelConfigFactory(ModelFactory[ProviderModelConfig]):
    __model__ = ProviderModelConfig


class ProviderConfigFactory(ModelFactory[ProviderConfig]):
    __model__ = ProviderConfig
    models = ()


class RoutingRuleConfigFactory(ModelFactory[RoutingRuleConfig]):
    __model__ = RoutingRuleConfig


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
    providers: dict[str, ProviderConfig] = {}  # noqa: RUF012
    config = CompanyConfig()
    budget = BudgetConfig()
    communication = CommunicationConfig()
    routing = RoutingConfig()
    logging = None


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
  anthropic:
    models:
      - id: test-model-001
        alias: sonnet
routing:
  strategy: cost_aware
  fallback_chain:
    - sonnet
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
  anthropic:
    base_url: ${ANTHROPIC_BASE_URL:-https://api.anthropic.com}
"""

ENV_VAR_MISSING_YAML = """\
company_name: ${UNDEFINED_VAR}
"""


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_root_config() -> RootConfig:
    return RootConfig(company_name="Test Corp")


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Callable[..., Path]:
    def _create(content: str, name: str = "config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _create
