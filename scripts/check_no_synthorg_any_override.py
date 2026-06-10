"""Regression gate: no ``synthorg.*`` mypy override may lift ``disallow_any_explicit``.

The global ``[tool.mypy] disallow_any_explicit = true`` holds across all of
``synthorg.*``; the irreducible explicit-``Any`` sites carry reasoned per-line
``# type: ignore[explicit-any]`` suppressions.

That global flag catches a bare explicit ``Any``, but a ``[[tool.mypy.overrides]]``
block can re-open the flag for a ``synthorg.*`` module while keeping mypy green, in
two ways: ``disallow_any_explicit = false``, or ``explicit-any`` listed in
``disable_error_code``. This gate parses ``pyproject.toml`` (no project import, so
it stays fast in pre-push) and fails when either form targets ``synthorg.*``. Only
the ``tests.*`` override may lift the flag.

Usage:
    uv run python scripts/check_no_synthorg_any_override.py
    uv run python scripts/check_no_synthorg_any_override.py --repo-root .

Exit codes:
    0 -- no ``synthorg.*`` override lifts ``disallow_any_explicit``.
    1 -- a forbidden override block was found.
    2 -- configuration error (missing or unparseable ``pyproject.toml``,
         or an invalid ``--repo-root``).
"""

import argparse
import fnmatch
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_PYPROJECT_REL: Final[str] = "pyproject.toml"


def _targets_synthorg(pattern: str) -> bool:
    """Return True if a mypy override ``module`` pattern covers ``synthorg`` code.

    mypy matches a ``module`` pattern against dotted module names with
    ``fnmatch``-style globbing, so a pattern targets synthorg if it matches the
    ``synthorg`` package itself or any ``synthorg.<sub>`` submodule. The literal
    prefix checks cover the common exact / dotted-wildcard forms
    (``synthorg``, ``synthorg.api.*``); the ``fnmatch`` probes additionally
    catch catch-all globs that would re-open the flag for synthorg without
    naming it (``*``, ``synthorg*``, ``synth*``), while leaving an unrelated
    ``synthorgX`` package untouched.
    """
    return (
        pattern == "synthorg"
        or pattern.startswith("synthorg.")
        or fnmatch.fnmatch("synthorg", pattern)
        or fnmatch.fnmatch("synthorg.probe", pattern)
    )


def _module_patterns(block: Mapping[str, object]) -> list[str]:
    """Return the ``module`` patterns of an override block (str or list form)."""
    modules = block.get("module")
    if isinstance(modules, str):
        return [modules]
    if isinstance(modules, list):
        return [pattern for pattern in modules if isinstance(pattern, str)]
    return []


def _lifts_explicit_any(block: Mapping[str, object]) -> bool:
    """Return True if an override block re-opens explicit ``Any`` for its modules.

    Two equivalent forms both suppress the ``explicit-any`` error: the boolean
    ``disallow_any_explicit = false`` and ``explicit-any`` appearing in
    ``disable_error_code`` (which may be a bare string or a list). Either keeps
    mypy green while allowing explicit ``Any`` again, so both count as lifting
    the flag.
    """
    if block.get("disallow_any_explicit") is False:
        return True
    disabled = block.get("disable_error_code")
    if isinstance(disabled, str):
        return disabled == "explicit-any"
    if isinstance(disabled, list):
        return "explicit-any" in disabled
    return False


def find_violations(data: Mapping[str, object]) -> list[str]:
    """Return every ``synthorg.*`` module pattern that lifts ``disallow_any_explicit``.

    Args:
        data: Parsed ``pyproject.toml`` contents.

    Returns:
        The offending ``module`` patterns, in source order. Empty when no
        ``synthorg.*`` override lifts the flag (via ``disallow_any_explicit =
        false`` or an ``explicit-any`` entry in ``disable_error_code``).
    """
    tool = data.get("tool")
    if not isinstance(tool, Mapping):
        return []
    mypy_cfg = tool.get("mypy")
    if not isinstance(mypy_cfg, Mapping):
        return []
    overrides = mypy_cfg.get("overrides")
    if not isinstance(overrides, list):
        return []

    violations: list[str] = []
    for block in overrides:
        if not isinstance(block, Mapping):
            continue
        if not _lifts_explicit_any(block):
            continue
        violations.extend(
            pattern for pattern in _module_patterns(block) if _targets_synthorg(pattern)
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    """Scan ``pyproject.toml`` and return the gate exit code.

    Exit codes are 0 (clean), 1 (a forbidden override was found), and 2
    (config error); see the module docstring for the full contract.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    pyproject = root / _PYPROJECT_REL
    try:
        raw = pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"error: could not read {_PYPROJECT_REL} under {root}: {exc}",
            file=sys.stderr,
        )
        return 2
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        print(f"error: could not parse {_PYPROJECT_REL}: {exc!s}", file=sys.stderr)
        return 2

    violations = find_violations(data)
    if not violations:
        return 0

    for pattern in violations:
        print(
            f"forbidden: [[tool.mypy.overrides]] block for module {pattern!r} "
            "lifts disallow_any_explicit (via disallow_any_explicit = false or "
            "an explicit-any entry in disable_error_code). The global "
            "disallow_any_explicit = true must hold for all of synthorg.*. "
            "Remove the override, or suppress an irreducible site with a "
            "reasoned # type: ignore[explicit-any]. Only the tests.* "
            "override may lift the flag.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
