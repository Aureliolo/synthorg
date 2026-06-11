"""Pre-commit / CI drift gate: timeout-check interval default.

``synthorg.api.lifecycle_helpers.boot_resolvers._DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS``
is the boot-time fallback the approval-timeout scheduler uses before the
settings resolver is wired. It MUST equal the registered default of
the ``security.timeout_check_interval_seconds`` setting so that a
deployment with no DB / env override behaves identically whether the
value flows through the registry or the boot fallback. A silent drift
between the two means the scheduler ticks at a different cadence than
the operator-visible setting claims.

This gate AST-parses both literals (no project import, so it stays
fast in pre-commit) and fails when they diverge.

Usage:
    uv run python scripts/check_timeout_interval_default_drift.py
    uv run python scripts/check_timeout_interval_default_drift.py --repo-root .

Exit codes:
    0 -- the two defaults agree.
    1 -- drift detected, or a required literal could not be found.
    2 -- configuration error (invalid ``--repo-root``, or a required
         source file could not be read or parsed -- fail-closed).
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        parse_source,
    )
else:
    from scripts._gate_source import GateSourceError, parse_source

_RESOLVERS_REL: Final[str] = "src/synthorg/api/lifecycle_helpers/boot_resolvers.py"
_SECURITY_REL: Final[str] = "src/synthorg/settings/definitions/security.py"
_CONSTANT_NAME: Final[str] = "_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS"
_SETTING_KEY: Final[str] = "timeout_check_interval_seconds"


def _module_constant(tree: ast.Module, name: str) -> float | None:
    """Return the float value of a module-level ``name: ... = <number>``."""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Name)
                and target.id == name
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (int, float))
            ):
                return float(node.value.value)
    return None


def _registered_default(tree: ast.Module, key: str) -> float | None:
    """Return the float of the ``default=`` on the SettingDefinition for *key*.

    Scans every ``SettingDefinition(...)`` call for one whose
    ``key=`` keyword is the literal *key*, then reads its ``default=``
    string/number literal and coerces to float.
    """
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SettingDefinition"
        ):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        key_node = kwargs.get("key")
        if not (isinstance(key_node, ast.Constant) and key_node.value == key):
            continue
        default_node = kwargs.get("default")
        if isinstance(default_node, ast.Constant):
            value = default_node.value
            if isinstance(value, (str, int, float)):
                try:
                    return float(value)
                except TypeError, ValueError:
                    return None
    return None


def main(argv: list[str] | None = None) -> int:
    """Compare the two defaults and return the gate exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        app_tree = parse_source(root / _RESOLVERS_REL)
        sec_tree = parse_source(root / _SECURITY_REL)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    constant = _module_constant(app_tree, _CONSTANT_NAME)
    registered = _registered_default(sec_tree, _SETTING_KEY)

    if constant is None:
        print(
            f"error: {_CONSTANT_NAME} not found in {_RESOLVERS_REL}",
            file=sys.stderr,
        )
        return 1
    if registered is None:
        print(
            f"error: default for security.{_SETTING_KEY} not found in {_SECURITY_REL}",
            file=sys.stderr,
        )
        return 1

    if constant != registered:
        print(
            "drift: boot fallback "
            f"{_CONSTANT_NAME}={constant} does not equal registered "
            f"security.{_SETTING_KEY} default={registered}. Update both "
            "to the same value.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
