"""Shared LOC counter + tier resolver for module-size gates.

Consumed by ``scripts/check_module_size_budget.py`` and the baseline
generator. Centralised here so the LOC-counting definition and the
``# module-kind:`` parsing rules cannot drift between the gate and the
baseline.

Behaviour:

* :func:`count_loc` strips blank lines and comment-only lines (mirrors
  ``check_baseline_growth.py::_count_text_entries``). Inline trailing
  comments DO count toward LOC.
* :func:`read_module_kind_header` returns the tier from a
  ``# module-kind: <tier>`` comment on the first non-blank,
  non-shebang, non-encoding-declaration line of a file. Strict
  position: a header after the module docstring or among imports is
  ignored. Unknown tier values raise ``ValueError``.
* :func:`resolve_tier` orchestrates: generated-glob -> ``generated``,
  ``tests/`` path -> ``tests``, explicit header wins, else ``code``.
* :func:`is_generated` matches ``*.gen.*`` and ``*_pb2.py``.

Naming note: this module deliberately does NOT contain ``_baseline``
in its filename so ``check_baseline_growth.py::_BASELINE_BASENAME_RE``
will not match it. It is a regular Python helper, not a baseline.
"""

import re
from pathlib import Path
from typing import Final

KNOWN_TIERS: Final[frozenset[str]] = frozenset(
    {
        "controller",
        "service",
        "orchestrator",
        "complex_service",
        "repository",
        "adapter",
        "integration",
        "feature",
        "code",
        "tests",
        "declarative",
        "generated",
    }
)

TIER_LIMITS: Final[dict[str, int | None]] = {
    "controller": 400,
    "service": 600,
    "orchestrator": 600,
    "complex_service": 1100,
    "repository": 500,
    "adapter": 700,
    "integration": 700,
    "feature": 100,
    "code": 500,
    "tests": 800,
    "declarative": None,
    "generated": None,
}

GENERATED_GLOBS: Final[tuple[str, ...]] = ("*.gen.*", "*_pb2.py")

_DEFAULT_TIER: Final[str] = "code"
_TESTS_TIER: Final[str] = "tests"
_GENERATED_TIER: Final[str] = "generated"

_MODULE_KIND_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*#\s*module-kind\s*:\s*(?P<tier>[A-Za-z_][A-Za-z0-9_]*)\s*$"
)

_SHEBANG_RE: Final[re.Pattern[str]] = re.compile(r"^#!\s*\S")

# PEP 263 encoding declaration; allowed in lines 1 and 2 only.
_ENCODING_RE: Final[re.Pattern[str]] = re.compile(r"^\s*#.*coding[=:]\s*([-\w.]+)")


class UnknownTierError(ValueError):
    """Raised when a ``# module-kind:`` header carries an unknown tier."""


def count_loc_text(text: str) -> int:
    """Count physical lines minus blank and comment-only lines, from text.

    Pure variant of :func:`count_loc` for callers that already hold the
    file's contents (avoids re-reading the file). One ``str.strip`` per
    line covers both the blank-line and comment-line tests: ``stripped``
    has no leading whitespace, so ``stripped[0] == "#"`` is equivalent to
    ``line.lstrip().startswith("#")``.

    Args:
        text: Full source text.

    Returns:
        Non-negative LOC count. Empty text returns 0.
    """
    return sum(
        1
        for line in text.splitlines()
        if (stripped := line.strip()) and stripped[0] != "#"
    )


def count_loc(path: Path) -> int:
    """Count physical lines minus blank lines and comment-only lines.

    Args:
        path: Path to the source file.

    Returns:
        Non-negative LOC count. An empty file returns 0.

    Raises:
        OSError: If the file cannot be read.
    """
    return count_loc_text(path.read_text(encoding="utf-8"))


def is_generated(filename: str) -> bool:
    """Return ``True`` iff *filename* matches any generated-glob pattern.

    Glob patterns: ``*.gen.*``, ``*_pb2.py``.

    Args:
        filename: Basename to test (no directory).

    Returns:
        ``True`` for generated files; ``False`` otherwise.
    """
    path = Path(filename)
    return any(path.match(pattern) for pattern in GENERATED_GLOBS)


def read_module_kind_header(path: Path) -> str | None:
    """Return the tier declared via ``# module-kind:`` header, or ``None``.

    The header must appear on the first non-blank, non-shebang,
    non-encoding-declaration line. Headers after the module docstring or
    interleaved with imports are ignored.

    Args:
        path: Path to the source file.

    Returns:
        The tier string if a valid header is present; ``None`` otherwise.

    Raises:
        UnknownTierError: If the header's tier value is not in
            :data:`KNOWN_TIERS`.
        OSError: If the file cannot be read.
    """
    return module_kind_from_text(path.read_text(encoding="utf-8"), path)


def module_kind_from_text(text: str, path: Path) -> str | None:
    """Return the ``# module-kind:`` tier from *text*, or ``None``.

    Pure variant of :func:`read_module_kind_header` for callers that
    already hold the file's contents. *path* is used only for the
    error message on an unknown tier.

    Raises:
        UnknownTierError: If the header's tier value is not in
            :data:`KNOWN_TIERS`.
    """
    for index, line in enumerate(text.splitlines()):
        if index == 0 and _SHEBANG_RE.match(line):
            continue
        if index <= 1 and _ENCODING_RE.match(line):
            continue
        if not line.strip():
            continue
        match = _MODULE_KIND_RE.match(line)
        if match is None:
            return None
        tier = match.group("tier")
        if tier not in KNOWN_TIERS:
            msg = (
                f"Unknown module-kind tier {tier!r} in {path}; "
                f"valid tiers: {sorted(KNOWN_TIERS)}"
            )
            raise UnknownTierError(msg)
        return tier
    return None


def resolve_tier(path: Path, *, project_root: Path) -> str:
    """Return the tier for a source file.

    Resolution order:

    1. Generated glob (``*.gen.*``, ``*_pb2.py``) -> ``generated``.
    2. ``tests/`` path prefix -> ``tests``.
    3. Explicit ``# module-kind:`` header -> declared tier.
    4. Default -> ``code``.

    Args:
        path: Path to the source file.
        project_root: Repo root, used to compute the relative path.

    Returns:
        One of the tiers in :data:`KNOWN_TIERS`.

    Raises:
        UnknownTierError: If the file declares an unknown tier.
    """
    if is_generated(path.name):
        return _GENERATED_TIER
    rel = path.relative_to(project_root).as_posix()
    if rel.startswith("tests/") or rel == "tests":
        return _TESTS_TIER
    header = read_module_kind_header(path)
    return header if header is not None else _DEFAULT_TIER


def resolve_tier_text(path: Path, text: str, *, rel_posix: str) -> str:
    """Resolve the tier from already-read *text* and a precomputed rel path.

    Pure variant of :func:`resolve_tier` for hot loops (e.g. the
    codebase-map build) that already hold the file's text and its
    repo-relative POSIX path, avoiding a second ``read_text`` and the
    cost of ``Path.relative_to``. ``rel_posix`` must be the file's path
    relative to the same root :func:`resolve_tier` uses as
    ``project_root`` (so the ``tests/`` prefix check is identical).

    Raises:
        UnknownTierError: If the file declares an unknown tier.
    """
    if is_generated(path.name):
        return _GENERATED_TIER
    if rel_posix.startswith("tests/") or rel_posix == "tests":
        return _TESTS_TIER
    header = module_kind_from_text(text, path)
    return header if header is not None else _DEFAULT_TIER
