#!/usr/bin/env python3
"""Gate: every ``scripts/check_*.py`` is actually invoked by something.

Nothing else checks this. ``check_convention_gate_inventory.py`` walks the
other direction (a MANDATORY doc paragraph must name a gate file that
exists on disk) and never enumerates the gates; vulture is scoped to
``src/synthorg`` and never looks at ``scripts/``. So a gate dropped from
``.pre-commit-config.yaml`` and not re-added to a runner keeps its file,
keeps its tests, keeps its documentation row, and simply stops running.
The convention it enforced is then unenforced, and the only signal is
that nobody notices. ``docs/reference/convention-gates.md`` already
records that two gates were added without a row before that gap was
spotted, which is the same blind spot seen from the other end.

Reachability is resolved from every place a gate can be invoked:

* a ``.pre-commit-config.yaml`` hook's ``entry`` or ``args``;
* ``run_prepush_python_gates._GATES``, the consolidated pre-push batch;
* ``run_prepush_hook_group._GROUPS``, the concurrent tool groups;
* ``run_edit_time_gates._GATES``, the PostToolUse dispatcher;
* any ``.github/workflows`` or ``.github/actions`` step, since a gate too
  slow or too dependency-heavy for a push legitimately lives in CI only;
* an agent hook in ``.claude/settings.json`` or the OpenCode plugin, where
  the edit-time gates run before a bad edit is ever written.

The three runners are imported rather than parsed. All three are
stdlib-only with guarded ``__main__`` blocks, so importing is cheap, and
re-implementing their declarations as regexes would drift from the tables
that actually decide what runs.

A gate with no automatic trigger by design is declared in
``scripts/unwired_gate_allowlist.yaml`` with a reason. That is a separate
file from ``convention_gate_map.yaml`` deliberately: entries there must
trace back to a MANDATORY doc paragraph, and "this one is intentionally
never invoked" has none.

Exit codes:
    0 -- every gate is reachable or allowlisted.
    1 -- a gate is unreachable, or an allowlist entry is stale.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR: Final[Path] = _REPO_ROOT / "scripts"
_PRE_COMMIT_CONFIG: Final[Path] = _REPO_ROOT / ".pre-commit-config.yaml"
_ALLOWLIST: Final[Path] = _SCRIPTS_DIR / "unwired_gate_allowlist.yaml"

# Matches a gate named anywhere in a hook's entry, including inside a
# ``bash -c '... && ...'`` compound, which is how several hooks chain.
_GATE_REFERENCE: Final[re.Pattern[str]] = re.compile(r"(check_[a-z0-9_]+)\.py")

_RUNNERS: Final[tuple[str, ...]] = (
    "run_prepush_python_gates",
    "run_prepush_hook_group",
    "run_edit_time_gates",
)


def _load_runner(stem: str) -> ModuleType:
    """Import one runner module by path, without importing ``scripts``."""
    spec = importlib.util.spec_from_file_location(stem, _SCRIPTS_DIR / f"{stem}.py")
    if spec is None or spec.loader is None:
        msg = f"could not load {stem}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_gates() -> set[str]:
    """Return every gate stem present on disk."""
    return {path.stem for path in _SCRIPTS_DIR.glob("check_*.py")}


def _reachable_from_pre_commit() -> set[str]:
    """Return gate stems named by any hook's entry or args."""
    data = yaml.safe_load(_PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    found: set[str] = set()
    for repo in data.get("repos", []):
        for hook in repo.get("hooks", []):
            text = (
                str(hook.get("entry", ""))
                + " "
                + " ".join(str(arg) for arg in hook.get("args", []))
            )
            found.update(_GATE_REFERENCE.findall(text))
    return found


def _reachable_from_runners() -> set[str]:
    """Return gate stems reachable through the three runner declarations."""
    found: set[str] = set()
    for stem in _RUNNERS:
        module = _load_runner(stem)
        declared = getattr(module, "_GATES", ())
        for entry in declared:
            # run_prepush_python_gates holds bare stems; run_edit_time_gates
            # holds objects carrying a script filename.
            text = entry if isinstance(entry, str) else getattr(entry, "script", "")
            found.update(_GATE_REFERENCE.findall(f"{text}.py"))
        for tools in getattr(module, "_GROUPS", {}).values():
            for tool in tools:
                found.update(_GATE_REFERENCE.findall(" ".join(tool.argv)))
    return found


def _reachable_from_ci() -> set[str]:
    """Return gate stems named anywhere under ``.github``.

    Matched against the raw text rather than parsed step by step: a gate
    can be invoked from a ``run:`` block, a composite action, a matrix
    entry or a shell one-liner, and the question here is only whether
    something invokes it at all.
    """
    found: set[str] = set()
    github = _REPO_ROOT / ".github"
    for pattern in ("workflows/**/*.yml", "workflows/**/*.yaml", "actions/**/*.yml"):
        for path in github.glob(pattern):
            found.update(
                _GATE_REFERENCE.findall(
                    path.read_text(encoding="utf-8", errors="replace")
                )
            )
    return found


def _reachable_from_agent_hooks() -> set[str]:
    """Return gate stems invoked by a Claude Code or OpenCode hook.

    An edit-time gate runs before a bad edit is written rather than at
    commit or push, so being absent from every other table is its normal
    state, not a defect.
    """
    found: set[str] = set()
    for path in (
        _REPO_ROOT / ".claude" / "settings.json",
        *(_REPO_ROOT / ".opencode" / "plugins").glob("*.ts"),
    ):
        if path.is_file():
            found.update(
                _GATE_REFERENCE.findall(
                    path.read_text(encoding="utf-8", errors="replace")
                )
            )
    return found


def _allowlisted() -> dict[str, str]:
    """Return ``stem -> reason`` for every deliberately unwired gate."""
    if not _ALLOWLIST.is_file():
        return {}
    data = yaml.safe_load(_ALLOWLIST.read_text(encoding="utf-8")) or {}
    entries = data.get("unwired_gates") or []
    allowed: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        script = str(entry.get("script", ""))
        reason = str(entry.get("reason", "")).strip()
        if script.endswith(".py") and reason:
            allowed[script.removesuffix(".py")] = reason
    return allowed


def check() -> list[str]:
    """Return one message per unreachable gate or stale allowlist entry."""
    declared = _declared_gates()
    reachable = (
        _reachable_from_pre_commit()
        | _reachable_from_runners()
        | _reachable_from_ci()
        | _reachable_from_agent_hooks()
    )
    allowed = _allowlisted()

    problems = [
        f"{stem}.py is never invoked: it is absent from"
        " .pre-commit-config.yaml, the three runner tables"
        " (run_prepush_python_gates._GATES, run_prepush_hook_group._GROUPS,"
        " run_edit_time_gates._GATES), every .github workflow or action, and"
        " every agent hook. Wire it into one of them, or declare it in"
        " scripts/unwired_gate_allowlist.yaml with a reason."
        for stem in sorted(declared - reachable - set(allowed))
    ]
    problems.extend(
        f"{stem}.py is allowlisted as unwired but does not exist; drop the"
        " entry from scripts/unwired_gate_allowlist.yaml."
        for stem in sorted(set(allowed) - declared)
    )
    problems.extend(
        f"{stem}.py is allowlisted as unwired but is now invoked; drop the"
        " entry from scripts/unwired_gate_allowlist.yaml so the exemption"
        " cannot outlive its reason."
        for stem in sorted(set(allowed) & reachable)
    )
    return problems


def main() -> int:
    """Report unreachable gates; return the shell exit code."""
    problems = check()
    for message in problems:
        print(message, file=sys.stderr)
    if problems:
        print(
            f"\n{len(problems)} gate wiring problem(s). A gate that runs"
            " nowhere enforces nothing, and nothing else in the tree"
            " notices.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
