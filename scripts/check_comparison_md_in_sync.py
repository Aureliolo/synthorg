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

Exit codes
----------
* ``0`` -- committed Markdown matches the freshly-generated output.
* ``1`` -- drift, or unreadable / unloadable inputs.
"""

import difflib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_OUTPUT_FILE: Final[Path] = REPO_ROOT / "docs" / "reference" / "comparison.md"
_GENERATOR_PATH: Final[Path] = REPO_ROOT / "scripts" / "generate_comparison.py"

_REMEDIATION: Final[str] = "Run: uv run python scripts/generate_comparison.py"


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
    except Exception as exc:
        print(
            f"error: could not generate expected comparison page: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

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
