"""Regression gate: no ``synthorg.*`` / ``tests.*`` mypy override may relax strictness.

The global ``[tool.mypy] disallow_any_explicit = true`` holds across all of
``synthorg.*`` AND ``tests.*``; the irreducible explicit-``Any`` sites carry
reasoned per-line ``# type: ignore[explicit-any]`` suppressions. A single
sanctioned base-code disable is permitted on the enforced surface --
``disable_error_code = ["prop-decorator"]`` on ``synthorg.*`` -- because
pydantic's ``@computed_field`` stacked on ``@property`` is correct usage mypy
cannot model. That is the ONLY allowlisted disable; every other error code (and
``explicit-any`` in particular) stays enforced.

A ``[[tool.mypy.overrides]]`` block can quietly relax strictness for a covered
module while keeping mypy green in three ways: ``disallow_any_explicit = false``;
a non-allowlisted code listed in ``disable_error_code``; or ``ignore_errors =
true`` (which silences every error). This gate parses ``pyproject.toml`` (no
project import, so it stays fast in pre-push) and fails when any of those forms
targets ``synthorg.*`` or ``tests.*`` -- including via a leading-wildcard
``module`` glob such as ``*.api`` that mypy compiles to a dot-spanning match. No
override may relax the enforced surface beyond the single ``prop-decorator``
allowlist entry.

The sibling gate ``check_no_explicit_any_inline_disable.py`` guards the other
vector: a module-level ``# mypy:`` comment inside a source file.

Usage:
    uv run python scripts/check_no_synthorg_any_override.py
    uv run python scripts/check_no_synthorg_any_override.py --repo-root .

Exit codes:
    0 -- no ``synthorg.*`` / ``tests.*`` override relaxes strictness beyond the
         allowlisted ``prop-decorator`` disable.
    1 -- a forbidden override block was found.
    2 -- configuration error (missing or unparseable ``pyproject.toml``,
         or an invalid ``--repo-root``).
"""

import argparse
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_PYPROJECT_REL: Final[str] = "pyproject.toml"


_ENFORCED_ROOTS: Final[tuple[str, ...]] = ("synthorg", "tests")

# The only error code an enforced-surface override may list in
# ``disable_error_code``. pydantic's ``@computed_field`` stacked on
# ``@property`` is correct usage mypy reports as ``prop-decorator``; disabling
# it globally for ``synthorg.*`` replaced ~120 inline ignores. Any other code
# (explicit-any included) remains a forbidden lift.
_ALLOWED_DISABLED_CODES: Final[frozenset[str]] = frozenset({"prop-decorator"})


def _targets_enforced(pattern: str) -> bool:
    """Return True if a mypy override ``module`` pattern covers enforced code.

    The enforced surface is ``synthorg.*`` and ``tests.*`` -- both inherit the
    global ``disallow_any_explicit = true`` and neither may carry a lifting
    override.

    mypy compiles a ``module`` override pattern with its own glob rules
    (``Options.compile_glob``): the pattern is split on ``.``; a component that
    is exactly ``*`` becomes a dot-spanning wildcard (a leading ``*`` ->
    ``.*``), while every other component is matched literally -- crucially, a
    ``*`` glued to other characters (``synthorg*``, ``*persistence``) is a
    *literal* asterisk that matches no real module. A pattern therefore covers
    enforced code in exactly two cases:

    * it names ``synthorg`` or ``tests`` literally -- the exact package, any
      dotted submodule, or a trailing-wildcard form (``synthorg``,
      ``synthorg.api``, ``tests.unit.*``); the dotted prefix keeps the check
      dotted so an unrelated adjacent package (``synthorgX``, ``testsuite``)
      stays untouched; or
    * its first dotted component is a bare ``*``, which compiles to a leading
      ``.*`` that spans the enforced prefix (``*``, ``*.api``,
      ``*.controllers.*``). Any other leading component is a literal that cannot
      match an enforced root.

    The bare-``*`` branch deliberately flags even a leading-``*`` glob whose
    suffix names no real module (``*.not_a_pkg``): such a flag-lift is either an
    effective project-wide weakening of ``disallow_any_explicit`` (must block) or
    a no-op misconfiguration (worth surfacing), so flagging is correct on both
    readings, and it never lets a real enforced-surface lift through.
    """
    for root in _ENFORCED_ROOTS:
        if pattern == root or pattern.startswith(f"{root}."):
            return True
    return pattern.split(".", 1)[0] == "*"


def _module_patterns(block: Mapping[str, object]) -> list[str]:
    """Return the ``module`` patterns of an override block (str or list form)."""
    modules = block.get("module")
    if isinstance(modules, str):
        return [modules]
    if isinstance(modules, list):
        return [pattern for pattern in modules if isinstance(pattern, str)]
    return []


def _disabled_codes(block: Mapping[str, object]) -> list[str]:
    """Return the ``disable_error_code`` entries of a block (str or list form)."""
    disabled = block.get("disable_error_code")
    if isinstance(disabled, str):
        return [disabled]
    if isinstance(disabled, list):
        return [code for code in disabled if isinstance(code, str)]
    return []


def _lifts_strictness(block: Mapping[str, object]) -> bool:
    """Return True if an override block relaxes strictness for its modules.

    Three forms each relax the enforced surface: the boolean
    ``disallow_any_explicit = false``; ``ignore_errors = true``, which silences
    *every* error for the matched modules; and a ``disable_error_code`` entry
    (string or list) naming any code outside :data:`_ALLOWED_DISABLED_CODES`.
    The single allowlisted ``prop-decorator`` disable is permitted; any other
    code -- ``explicit-any`` included -- keeps mypy green while weakening the
    contract, so all such forms count as a lift.
    """
    if (
        block.get("disallow_any_explicit") is False
        or block.get("ignore_errors") is True
    ):
        return True
    return any(code not in _ALLOWED_DISABLED_CODES for code in _disabled_codes(block))


def find_violations(data: Mapping[str, object]) -> list[str]:
    """Return every enforced-surface module pattern that relaxes strictness.

    Args:
        data: Parsed ``pyproject.toml`` contents.

    Returns:
        The offending ``module`` patterns (``synthorg.*`` or ``tests.*``), in
        source order. Empty when no enforced-surface override relaxes strictness
        (via ``disallow_any_explicit = false``, a non-allowlisted entry in
        ``disable_error_code``, or ``ignore_errors = true``); the single
        ``prop-decorator`` disable is allowlisted and never an offender.
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
        if not _lifts_strictness(block):
            continue
        violations.extend(
            pattern for pattern in _module_patterns(block) if _targets_enforced(pattern)
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
            "relaxes strictness (via disallow_any_explicit = false, a "
            "non-allowlisted entry in disable_error_code, or ignore_errors = "
            "true). The enforced surface synthorg.* / tests.* may only carry the "
            "allowlisted disable_error_code = ['prop-decorator']. Remove the "
            "override, or suppress an irreducible site with a reasoned per-line "
            "# type: ignore[<code>]. No override may relax either surface "
            "further.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
