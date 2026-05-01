"""Coverage guard: every controller decorator site uses the registry.

This test AST-walks every controller file and fails loud if a bare
``per_op_rate_limit(`` call ever appears, if a new policy key is
referenced that has not been registered in
:data:`RATE_LIMIT_POLICIES`, or if a specific endpoint loses its
expected policy guard.
"""

import ast
from pathlib import Path

import pytest

from synthorg.api.rate_limits.policies import RATE_LIMIT_POLICIES

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CONTROLLERS_DIR = _REPO_ROOT / "src" / "synthorg" / "api" / "controllers"
_A2A_GATEWAY_FILE = _REPO_ROOT / "src" / "synthorg" / "a2a" / "gateway.py"
_AUTH_CONTROLLER_FILE = (
    _REPO_ROOT / "src" / "synthorg" / "api" / "auth" / "controller.py"
)


def _controller_files() -> list[Path]:
    """Every ``*.py`` directly inside ``src/synthorg/api/controllers``."""
    return sorted(p for p in _CONTROLLERS_DIR.glob("*.py") if p.name != "__init__.py")


def _guarded_source_files() -> list[Path]:
    """Every file that may carry a ``per_op_rate_limit_from_policy`` site.

    Includes the controllers directory plus the two stand-alone modules
    that own rate-limited entry points outside ``api/controllers/``
    (``a2a/gateway.py`` and ``api/auth/controller.py``). The AST-wide
    assertions below scan this superset so neither stand-alone module
    can drift back to a bare ``per_op_rate_limit`` call without the
    test failing.
    """
    return sorted(
        {
            *_controller_files(),
            _A2A_GATEWAY_FILE,
            _AUTH_CONTROLLER_FILE,
        }
    )


def test_controllers_directory_discovered() -> None:
    # Sanity: the path math above must land on a non-empty dir;
    # otherwise the coverage assertions below would be vacuously true.
    files = _controller_files()
    assert files, f"no controller files discovered at {_CONTROLLERS_DIR}"


def _call_target_name(node: ast.Call) -> str | None:
    """Return the final attribute name of a call target, or ``None``.

    Handles both bare-name calls (``foo(...)``) and attribute-access
    calls (``module.foo(...)`` / ``pkg.sub.foo(...)``).  Returning the
    *final* segment lets a single regex catch aliased imports, module
    re-exports, and attribute-chain access uniformly.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_no_residual_bare_per_op_rate_limit_calls() -> None:
    """No guarded site may call the primitive decorator directly."""
    offenders: list[str] = []
    for path in _guarded_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_target_name(node) == "per_op_rate_limit"
        )
    assert not offenders, (
        "Guarded sites must use per_op_rate_limit_from_policy instead "
        "of the bare per_op_rate_limit primitive. Offending sites: "
        f"{offenders!r}"
    )


def test_every_policy_lookup_resolves() -> None:
    """Every string passed to the helper must exist in the registry."""
    unknown: list[tuple[str, str, int]] = []
    for path in _guarded_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_target_name(node) != "per_op_rate_limit_from_policy":
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                # A non-literal first arg is unusual but not wrong;
                # the runtime KeyError remains the safety net.
                continue
            if first.value not in RATE_LIMIT_POLICIES:
                unknown.append((path.name, first.value, node.lineno))
    assert not unknown, (
        "Guarded sites reference policy keys missing from "
        "RATE_LIMIT_POLICIES: "
        + ", ".join(f"{name}:{line} -> {op!r}" for name, op, line in unknown)
    )


# Per-endpoint guard wiring: each tuple is
# (file_relative_to_repo_root, function_name, expected_operation_key).
# When the named function loses its ``per_op_rate_limit_from_policy``
# guard or the operation key drifts, this test fails loud and points
# at the exact site.
_GUARDED_ENDPOINTS: tuple[tuple[Path, str, str], ...] = (
    (
        _CONTROLLERS_DIR / "simulations.py",
        "cancel_simulation",
        "simulations.cancel",
    ),
    (
        _CONTROLLERS_DIR / "artifacts.py",
        "create_artifact",
        "artifacts.create",
    ),
    (
        _CONTROLLERS_DIR / "events.py",
        "resume_interrupt",
        "interrupts.resume",
    ),
    (
        _CONTROLLERS_DIR / "events.py",
        "resume",
        "interrupts.resume",
    ),
    (
        _CONTROLLERS_DIR / "autonomy.py",
        "update_autonomy",
        "agents.autonomy_change",
    ),
    (
        _CONTROLLERS_DIR / "coordination.py",
        "coordinate_task",
        "tasks.coordinate",
    ),
    (
        _CONTROLLERS_DIR / "clients.py",
        "create_client",
        "clients.create",
    ),
    (
        _CONTROLLERS_DIR / "collaboration.py",
        "set_override",
        "collaboration.override",
    ),
    (
        _CONTROLLERS_DIR / "collaboration.py",
        "clear_override",
        "collaboration.override",
    ),
    (
        _CONTROLLERS_DIR / "company.py",
        "reorder_departments",
        "company.reorder_departments",
    ),
    (
        _CONTROLLERS_DIR / "meta.py",
        "trigger_cycle",
        "meta.trigger_cycle",
    ),
    (
        _CONTROLLERS_DIR / "meta_analytics.py",
        "ingest_events",
        "meta.ingest_events",
    ),
    (_A2A_GATEWAY_FILE, "handle_jsonrpc", "a2a.gateway"),
    (_AUTH_CONTROLLER_FILE, "ws_ticket", "auth.ws_ticket"),
)


def _function_decorator_policy_keys(
    tree: ast.Module, function_name: str
) -> tuple[set[str], int | None]:
    """Return the set of policy keys referenced by a function's decorators.

    Walks every decorator on every ``async def``/``def`` named
    ``function_name`` and collects every literal string passed as the
    first arg to ``per_op_rate_limit_from_policy``.  Decorator
    arguments are inspected too -- the helper is invoked inside
    ``guards=[...]`` lists on ``@post(...)``/``@delete(...)``, so the
    walk has to descend into the decorator AST, not just the name.
    """
    policy_keys: set[str] = set()
    line: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name != function_name:
            continue
        line = node.lineno
        for decorator in node.decorator_list:
            for sub in ast.walk(decorator):
                if not isinstance(sub, ast.Call):
                    continue
                if _call_target_name(sub) != "per_op_rate_limit_from_policy":
                    continue
                if not sub.args:
                    continue
                first = sub.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    policy_keys.add(first.value)
    return policy_keys, line


@pytest.mark.parametrize(
    ("path", "function_name", "expected_key"),
    _GUARDED_ENDPOINTS,
    ids=[f"{p.name}::{fn}" for p, fn, _ in _GUARDED_ENDPOINTS],
)
def test_endpoint_carries_expected_policy_guard(
    path: Path,
    function_name: str,
    expected_key: str,
) -> None:
    """Each named endpoint must carry the expected policy guard."""
    assert path.exists(), f"endpoint source file missing: {path}"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    policy_keys, line = _function_decorator_policy_keys(tree, function_name)
    assert line is not None, f"function {function_name!r} not found in {path.name}"
    assert expected_key in policy_keys, (
        f"{path.name}::{function_name} (line {line}) does not reference "
        f"per_op_rate_limit_from_policy({expected_key!r}) -- "
        f"found policy keys: {sorted(policy_keys) or 'none'}"
    )
