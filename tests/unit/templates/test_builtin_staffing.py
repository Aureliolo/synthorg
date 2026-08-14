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

from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.templates.enums import PostureName
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


@pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
def test_builtin_template_staffs_a_completion_reviewer(name: str) -> None:
    """A shipped org must be able to review its own finished work.

    The completion oracle is on by default from LOW stakes and excludes the
    executor, so a template that staffs no holder of the role ships an
    organisation whose every reviewed task parks BLOCKED on the first
    deliverable it produces.
    """
    config = render_template(load_template(name))

    roles = [agent.role for agent in config.agents]
    assert COMPLETION_REVIEWER_ROLE_NAME in roles, (
        f"{name} staffs no {COMPLETION_REVIEWER_ROLE_NAME}: {sorted(roles)}"
    )


@pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
def test_a_security_hardened_template_staffs_a_red_teamer(name: str) -> None:
    """The hardened posture arms the adversarial gate, so it needs a holder.

    The red-team gate is fail-OPEN on a verifier defect but fail-CLOSED on
    being unstaffed, which is a configuration state rather than a defect: a
    hardened template that turns the gate on without staffing it would block
    every deliverable at or above its stakes floor.
    """
    loaded = load_template(name)
    if loaded.template.posture is not PostureName.SECURITY_HARDENED:
        pytest.skip(f"{name} does not arm the red-team gate")

    roles = [agent.role for agent in render_template(loaded).agents]
    assert RED_TEAM_ROLE_NAME in roles, (
        f"{name} arms the red-team gate but staffs no "
        f"{RED_TEAM_ROLE_NAME}: {sorted(roles)}"
    )


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
