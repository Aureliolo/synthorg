#!/usr/bin/env python3
"""Pre-push + CI gate: ``docs/reference/comparison.md`` must be in sync.

The comparison page is generated from ``data/competitors.yaml`` by
``scripts/generate_comparison.py``. A change to the YAML that is not
followed by a regenerate-and-commit leaves the rendered Markdown stale,
so the public comparison table silently drifts from the source data.
This gate fails the build before that lands: it re-runs the generator
in-memory and compares the result against the committed file.

The gate never writes the file; it only reads. Remediation is a single
generator run.

One rendered line is excluded from the comparison, and only when the YAML
declares the ``auto`` sentinel: the "Comparison data last changed" date, which
the generator derives from the committer date of the last commit touching
``data/competitors.yaml``. A squash-merge mints a new commit with a new date,
so the date the page must carry changes at the moment of merge, after the last
point anyone could have regenerated it. That left main red on a page whose
content was correct, and it recurred on every merge touching the YAML, because
the gate was asserting a property the merge itself rewrites. A pinned date is
authored content and stays compared: it cannot be changed by a merge, so drift
there is real.

Exit codes
----------
* ``0`` -- committed Markdown matches the freshly-generated output.
* ``1`` -- drift, or unreadable / unloadable inputs.
"""

import difflib
import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml
from pydantic import BaseModel, ConfigDict

from synthorg.core.boundary import parse_typed
from synthorg.observability import safe_error_description

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_OUTPUT_FILE: Final[Path] = REPO_ROOT / "docs" / "reference" / "comparison.md"
_GENERATOR_PATH: Final[Path] = REPO_ROOT / "scripts" / "generate_comparison.py"

_REMEDIATION: Final[str] = "Run: uv run python scripts/generate_comparison.py"

#: The one rendered line whose value the generator derives from git rather than
#: from the YAML, and which a merge therefore rewrites out from under a
#: correctly-generated page.
_DERIVED_DATE_LINE: Final[re.Pattern[str]] = re.compile(
    r"^(Comparison data last changed: ).*$", re.MULTILINE
)
_DERIVED_DATE_MASK: Final[str] = r"\1<derived from git>"


class _CompetitorsMeta(BaseModel):
    """The ``meta`` block of ``data/competitors.yaml``.

    Only ``last_updated`` is read here; the generator owns the rest. Extra keys
    are accepted so a field added for the generator does not fail this gate.
    """

    model_config = ConfigDict(frozen=True, extra="allow", allow_inf_nan=False)

    last_updated: str | None = None


class _CompetitorsFile(BaseModel):
    """The top level of ``data/competitors.yaml``, as far as this gate reads."""

    model_config = ConfigDict(frozen=True, extra="allow", allow_inf_nan=False)

    meta: _CompetitorsMeta = _CompetitorsMeta()


def _load_generator() -> ModuleType:
    """Import ``scripts/generate_comparison.py`` to reuse its renderer."""
    spec = importlib.util.spec_from_file_location(
        "generate_comparison", _GENERATOR_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load generator at {_GENERATOR_PATH}"
        raise RuntimeError(msg)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        msg = f"could not import {_GENERATOR_PATH}: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc
    return mod


def _expected_markdown(gen_mod: ModuleType) -> str:
    """Return the Markdown the generator would write for the current YAML."""
    data = gen_mod._load_data()  # noqa: SLF001
    markdown: str = gen_mod._generate_markdown(data)  # noqa: SLF001
    return markdown


def _declares_auto_date(gen_mod: ModuleType) -> bool:
    """Report whether the YAML asks for a git-derived date.

    Read from the raw file rather than from ``_load_data``, which has already
    resolved the sentinel into a date and cannot say which it was.

    Args:
        gen_mod: The imported generator, for its sentinel and data path.

    Returns:
        True when ``meta.last_updated`` is the auto sentinel.
    """
    raw = yaml.safe_load(gen_mod.DATA_FILE.read_text(encoding="utf-8"))
    parsed = parse_typed("comparison.yaml", raw, _CompetitorsFile)
    return bool(parsed.meta.last_updated == gen_mod.AUTO_SENTINEL)


def _mask_derived_date(text: str) -> str:
    """Blank the git-derived date so a merge-rewritten commit date is not drift.

    Args:
        text: Rendered Markdown, committed or freshly generated.

    Returns:
        The same text with the derived date line's value replaced.
    """
    return _DERIVED_DATE_LINE.sub(_DERIVED_DATE_MASK, text)


def main() -> int:
    """Compare committed comparison.md against the generator; return exit code."""
    if not _OUTPUT_FILE.is_file():
        print(
            f"error: {_OUTPUT_FILE} does not exist; cannot check sync.\n{_REMEDIATION}",
            file=sys.stderr,
        )
        return 1

    try:
        committed = _OUTPUT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"error: could not read {_OUTPUT_FILE}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    # A gate must fail with a clean diagnostic, never a raw traceback. The
    # generator opens data/competitors.yaml (OSError/yaml.YAMLError on a bad
    # read) and is reached via untyped module attributes (AttributeError if its
    # private API is renamed), so catch broadly and map every failure to exit 1.
    try:
        gen_mod = _load_generator()
        expected = _expected_markdown(gen_mod)
        auto_date = _declares_auto_date(gen_mod)
    except Exception as exc:
        # Never interpolate a raw exception: the typed parse above raises a
        # ValidationError carrying whatever the YAML held, and this line lands
        # in CI output. The helper already leads with the exception type, so
        # naming it again reads back as `ValidationError: ValidationError: ...`.
        print(
            f"error: could not generate expected comparison page: "
            f"{safe_error_description(exc)}",
            file=sys.stderr,
        )
        return 1

    if auto_date:
        committed = _mask_derived_date(committed)
        expected = _mask_derived_date(expected)

    # The generator joins lines without a trailing newline; normalise so a
    # final-newline-only difference does not flap the gate.
    if committed.rstrip("\n") == expected.rstrip("\n"):
        print(f"OK: {_OUTPUT_FILE.relative_to(REPO_ROOT)} matches the generator.")
        return 0

    return _report_drift(committed, expected)


def _report_drift(committed: str, expected: str) -> int:
    """Print a unified diff of generated vs committed Markdown; return exit 1."""
    diff = difflib.unified_diff(
        expected.splitlines(),
        committed.splitlines(),
        fromfile="generated",
        tofile="committed",
        lineterm="",
    )
    print(
        f"{_OUTPUT_FILE.relative_to(REPO_ROOT)} is out of sync with "
        "data/competitors.yaml:",
        file=sys.stderr,
    )
    for line in diff:
        print(line, file=sys.stderr)
    print(_REMEDIATION, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
