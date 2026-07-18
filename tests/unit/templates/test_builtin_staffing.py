"""Staffing standard for the shipped builtin company templates.

Every builtin template must render into a runnable organisation: at least one
department, at least one agent, and every declared department staffed by at
least one agent. The "no unstaffed department" half is enforced for ALL
rendered templates by ``_validate_staffing`` (a render of an under-staffed
template raises); this suite additionally holds the shipped builtins to the
"at least one of each department, at least one agent" half that a minimal
ad-hoc render is not required to meet.
"""

import pytest

from synthorg.templates.errors import TemplateValidationError
from synthorg.templates.loader import (
    BUILTIN_TEMPLATES,
    load_template,
    load_template_file,
)
from synthorg.templates.renderer import render_template
from tests.unit.templates.conftest import TemplateFileFactory

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
def test_builtin_template_is_fully_staffed(name: str) -> None:
    # render_template already rejects an unstaffed department (raises), so a
    # successful render proves every declared department has an agent; here we
    # additionally require the org to be non-empty.
    config = render_template(load_template(name))

    assert config.departments, f"{name} declares no departments"
    assert config.agents, f"{name} declares no agents"

    staffed = {agent.department for agent in config.agents}
    unstaffed = sorted(d.name for d in config.departments if d.name not in staffed)
    assert not unstaffed, f"{name} has unstaffed departments: {unstaffed}"


def test_render_rejects_a_declared_department_with_no_agent(
    tmp_template_file: TemplateFileFactory,
) -> None:
    # "sales" is declared but the only agent works in "engineering": the render
    # must reject the hollow department rather than ship an empty org unit.
    yaml_content = """\
template:
  name: "Unstaffed Department"
  description: "test"
  version: "1.0.0"
  company:
    type: "custom"
  departments:
    - name: "sales"
      budget_percent: 100
  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      department: "engineering"
"""
    loaded = load_template_file(tmp_template_file(yaml_content))
    with pytest.raises(TemplateValidationError, match="unstaffed"):
        render_template(loaded)
