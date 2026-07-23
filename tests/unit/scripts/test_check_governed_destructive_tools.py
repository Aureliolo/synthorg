"""Tests for the destructive-governed-tool convention gate.

A gate that only ever passes is worthless, so these pin both directions:
the real tree stays clean, and each violation the gate exists to catch is
actually caught.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_GATE = Path("scripts/check_governed_destructive_tools.py")

_GUARDED_TOOL = '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.security.autonomy.enums import ActionType


class GuardedTool:
    _DESTRUCTIVE: ClassVar[bool] = True
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value

    def _check_preconditions(self, args: object) -> None:
        """Guard first."""
        reason, actor = require_admin_guardrails({}, None)
'''

_NO_GUARDRAIL = '''
from typing import ClassVar

from synthorg.security.autonomy.enums import ActionType


class UnguardedTool:
    _DESTRUCTIVE: ClassVar[bool] = True
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value

    def _check_preconditions(self, args: object) -> None:
        """No guardrail at all."""
        return None
'''

_GUARDRAIL_NOT_FIRST = '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.security.autonomy.enums import ActionType


class LateGuardTool:
    _DESTRUCTIVE: ClassVar[bool] = True
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value

    def _check_preconditions(self, args: object) -> None:
        """Something else runs before the guardrail."""
        self._log(args)
        reason, actor = require_admin_guardrails({}, None)
'''

_SHARED_ACTION_TYPE = '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails


class SharedTypeTool:
    _DESTRUCTIVE: ClassVar[bool] = True

    def _check_preconditions(self, args: object) -> None:
        """Guarded, but inherits the shared action type."""
        reason, actor = require_admin_guardrails({}, None)
'''

_INHERITED_ACTION_TYPE = '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.security.autonomy.enums import ActionType


class FamilyBase:
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value


class ConcreteTool(FamilyBase):
    _DESTRUCTIVE: ClassVar[bool] = True

    def _check_preconditions(self, args: object) -> None:
        """Guarded; action type comes from the family base."""
        reason, actor = require_admin_guardrails({}, None)
'''

_OPTED_OUT = """
from typing import ClassVar


class OptedOutTool:  # lint-allow: governed-destructive -- covered elsewhere
    _DESTRUCTIVE: ClassVar[bool] = True
"""

_NON_DESTRUCTIVE_DEPLOY_READ = '''
from typing import ClassVar

from synthorg.security.autonomy.enums import ActionType


class DeployReadTool:
    """Carries the family action type for risk, but causes nothing."""

    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value
'''


# Two modules defining the same base name. The one that binds the action
# type sits in the module that also holds the destructive tool; the inert
# twin sorts *after* it, so a resolver keyed on the bare name alone would
# keep the twin and report the tool as unbound.
_LOCAL_BASE_WINS: dict[str, str] = {
    "a_family.py": '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails
from synthorg.security.autonomy.enums import ActionType


class FamilyBase:
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value


class ConcreteTool(FamilyBase):
    _DESTRUCTIVE: ClassVar[bool] = True

    def _check_preconditions(self, args: object) -> None:
        """Guarded; action type comes from this module's family base."""
        reason, actor = require_admin_guardrails({}, None)
''',
    "z_unrelated.py": '''
class FamilyBase:
    """An unrelated class that happens to share the name."""
''',
}

# The destructive tool's base is defined only in *other* modules, and by
# more than one of them, so nothing picks a winner honestly.
_AMBIGUOUS_BASE: dict[str, str] = {
    "tool.py": '''
from typing import ClassVar

from synthorg.meta.mcp.handlers.common import require_admin_guardrails

from synthorg.tools.a_family import FamilyBase


class ConcreteTool(FamilyBase):
    _DESTRUCTIVE: ClassVar[bool] = True

    def _check_preconditions(self, args: object) -> None:
        """Guarded, but the base name is defined in two other modules."""
        reason, actor = require_admin_guardrails({}, None)
''',
    "a_family.py": """
from typing import ClassVar

from synthorg.security.autonomy.enums import ActionType


class FamilyBase:
    _ACTION_TYPE: ClassVar[str] = ActionType.DEPLOY_PRODUCTION.value
""",
    "z_family.py": '''
class FamilyBase:
    """A same-named base binding no action type."""
''',
}


def _run_gate(root: Path) -> subprocess.CompletedProcess[str]:
    # Fixed argv of interpreter + gate path + a tmp_path root; no shell, and
    # no caller-supplied string reaches the command line.
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, no untrusted input
        [sys.executable, str(_GATE.resolve()), "--repo-root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _tree(tmp_path: Path, source: str) -> Path:
    return _multi_module_tree(tmp_path, {"sample.py": source})


def _multi_module_tree(tmp_path: Path, modules: dict[str, str]) -> Path:
    tools = tmp_path / "src" / "synthorg" / "tools"
    tools.mkdir(parents=True)
    for name, source in modules.items():
        (tools / name).write_text(source, encoding="utf-8")
    return tmp_path


def test_real_tree_is_clean() -> None:
    result = _run_gate(Path())
    assert result.returncode == 0, result.stderr


def test_guarded_destructive_tool_passes(tmp_path: Path) -> None:
    result = _run_gate(_tree(tmp_path, _GUARDED_TOOL))
    assert result.returncode == 0, result.stderr


def test_missing_guardrail_is_caught(tmp_path: Path) -> None:
    result = _run_gate(_tree(tmp_path, _NO_GUARDRAIL))
    assert result.returncode == 1
    assert "require_admin_guardrails" in result.stderr


def test_guardrail_not_first_is_caught(tmp_path: Path) -> None:
    """A guardrail that runs after other work is not a precondition."""
    result = _run_gate(_tree(tmp_path, _GUARDRAIL_NOT_FIRST))
    assert result.returncode == 1
    assert "first statement" in result.stderr


def test_shared_action_type_is_caught(tmp_path: Path) -> None:
    """The auto-approve hole: destructive tools must not share comms."""
    result = _run_gate(_tree(tmp_path, _SHARED_ACTION_TYPE))
    assert result.returncode == 1
    assert "_ACTION_TYPE" in result.stderr


def test_action_type_inherited_from_a_family_base_is_accepted(
    tmp_path: Path,
) -> None:
    result = _run_gate(_tree(tmp_path, _INHERITED_ACTION_TYPE))
    assert result.returncode == 0, result.stderr


def test_opt_out_marker_is_honoured(tmp_path: Path) -> None:
    result = _run_gate(_tree(tmp_path, _OPTED_OUT))
    assert result.returncode == 0, result.stderr


def test_read_only_tool_on_a_deploy_type_is_not_required_to_be_destructive(
    tmp_path: Path,
) -> None:
    """Observing a deployment carries deploy risk without causing anything."""
    result = _run_gate(_tree(tmp_path, _NON_DESTRUCTIVE_DEPLOY_READ))
    assert result.returncode == 0, result.stderr


def test_a_same_named_base_elsewhere_does_not_mask_the_local_one(
    tmp_path: Path,
) -> None:
    """Base lookup is by bare name, so it must prefer the defining module.

    Resolving to whichever module happened to be scanned last would
    attribute one family's action type to another's tool: the exact
    mis-attribution that could let an unguarded destructive tool pass.
    """
    result = _run_gate(_multi_module_tree(tmp_path, _LOCAL_BASE_WINS))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_ambiguous_base_name_fails_closed(tmp_path: Path) -> None:
    """Unresolvable is reported, never guessed."""
    result = _run_gate(_multi_module_tree(tmp_path, _AMBIGUOUS_BASE))
    assert result.returncode == 1
    assert "defined in several modules" in result.stdout + result.stderr


def test_missing_tools_package_fails_closed(tmp_path: Path) -> None:
    result = _run_gate(tmp_path)
    assert result.returncode == 2
