"""Unit test configuration and fixtures for templates."""

from typing import TYPE_CHECKING, Any, Protocol

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class TemplateFileFactory(Protocol):
    """Callable signature for the tmp_template_file fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


MINIMAL_TEMPLATE_YAML = """\
template:
  name: "Test Template"
  description: "A minimal test template"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
"""

TEMPLATE_WITH_VARIABLES_YAML = """\
template:
  name: "Var Template"
  description: "Template with variables"
  version: "1.0.0"

  variables:
    - name: "company_name"
      description: "Name of your company"
      default: "Default Corp"
    - name: "budget"
      description: "Monthly budget"
      var_type: "float"
      default: 42.0

  company:
    type: "startup"
    budget_monthly: {{ budget | default(42.0) }}
    autonomy: 0.7

  departments:
    - name: "engineering"
      budget_percent: 100
      head_role: "Backend Developer"

  agents:
    - role: "Backend Developer"
      name: "{{ company_name }} Dev"
      level: "senior"
      model: "medium"
      department: "engineering"
"""

TEMPLATE_REQUIRED_VAR_YAML = """\
template:
  name: "Required Var"
  description: "Has a required variable"
  version: "1.0.0"

  variables:
    - name: "team_lead"
      description: "Name of the team lead"
      required: true

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "{{ team_lead }}"
      level: "mid"
      model: "medium"
      department: "engineering"
"""

INVALID_SYNTAX_YAML = """\
template:
  name: "Bad YAML"
  agents: [unterminated
"""

MISSING_TEMPLATE_KEY_YAML = """\
name: "No Template Key"
agents: []
"""

CHILD_EXTENDS_STARTUP_YAML = """\
template:
  name: "Child of Startup"
  description: "Extends startup with extra agents"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  extends: "startup"

  company:
    type: "startup"

  agents:
    - role: "QA Engineer"
      level: "mid"
      model: "small"
      personality_preset: "quality_guardian"
      department: "engineering"
"""

CHILD_OVERRIDE_AGENT_YAML = """\
template:
  name: "Override Child"
  description: "Overrides a parent agent"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  extends: "solo_founder"

  company:
    type: "solo_founder"

  agents:
    - role: "Full-Stack Developer"
      level: "lead"
      model: "large"
      personality_preset: "visionary_leader"
      department: "engineering"
"""

CHILD_REMOVE_AGENT_YAML = """\
template:
  name: "Remove Child"
  description: "Removes a parent agent"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  extends: "solo_founder"

  company:
    type: "solo_founder"

  agents:
    - role: "Full-Stack Developer"
      department: "engineering"
      _remove: true
    - role: "Backend Developer"
      level: "senior"
      model: "medium"
      personality_preset: "pragmatic_builder"
      department: "engineering"
"""

CIRCULAR_SELF_YAML = """\
template:
  name: "Self Loop"
  description: "Extends itself"
  version: "1.0.0"
  min_agents: 1
  max_agents: 10
  extends: "self_loop"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
"""


def _make_template_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid CompanyTemplate kwargs dict with overrides."""
    base: dict[str, Any] = {
        "metadata": {
            "name": "Test",
            "description": "desc",
            "version": "1.0.0",
            "company_type": "custom",
        },
        "agents": (
            {
                "role": "Backend Developer",
                "level": "mid",
                "model": "medium",
            },
        ),
    }
    base.update(overrides)
    return base


@pytest.fixture
def make_template_dict() -> Callable[..., dict[str, Any]]:
    """Factory fixture for building template kwargs dicts."""
    return _make_template_dict


@pytest.fixture
def tmp_template_file(tmp_path: Path) -> TemplateFileFactory:
    """Factory fixture for writing a temporary template YAML file."""

    def _create(content: str, name: str = "test_template.yaml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _create
