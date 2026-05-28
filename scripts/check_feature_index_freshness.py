#!/usr/bin/env python3
# module-kind: code
"""Feature-index freshness gate.

Regenerates ``data/feature_index.json`` + ``data/codebase_map.json`` to a
scratch path and asserts the committed files match byte-for-byte. The gate
is fail-closed: missing artefacts fail; stale artefacts fail. A PR that
modifies any feature manifest must regenerate and commit the index.

The :mod:`scripts.generate_feature_index` module owns the regeneration;
this gate is a thin wrapper that drives it and diffs the result. The
``generated_at`` timestamp is ignored when diffing so a clean regeneration
on the same tree always matches.

Run from the repo root::

    uv run python scripts/check_feature_index_freshness.py
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_GENERATOR_SCRIPT: Final[str] = "scripts/generate_feature_index.py"

FEATURE_INDEX_REL: Final[Path] = Path("data") / "feature_index.json"
CODEBASE_MAP_REL: Final[Path] = Path("data") / "codebase_map.json"

_GENERATED_AT_KEY: Final[str] = "generated_at"


def _load_generator(repo_root: Path) -> object:
    """Import the generator module from *repo_root*."""
    script_path = repo_root / _GENERATOR_SCRIPT
    spec = importlib.util.spec_from_file_location("generate_feature_index", script_path)
    if spec is None or spec.loader is None:
        msg = f"cannot load generator at {script_path}"
        raise OSError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strip_generated_at(payload: dict[str, object]) -> dict[str, object]:
    """Return a copy of *payload* with the volatile timestamp removed."""
    return {key: value for key, value in payload.items() if key != _GENERATED_AT_KEY}


def _load_json(path: Path) -> object:
    """Return ``json.loads`` of *path*'s contents."""
    return json.loads(path.read_text(encoding="utf-8"))


def check(*, repo_root: Path) -> list[str]:
    """Return findings (empty == artefacts match the freshly-regenerated state).

    Both files must exist; if missing, fail-closed. The committed JSON
    must match the regenerated JSON modulo the ``generated_at`` field of
    the feature index (which moves on every run by design).

    Args:
        repo_root: Repository root containing ``data/`` and ``scripts/``.

    Returns:
        List of human-readable findings; empty list means the gate passes.
    """
    findings: list[str] = []
    index_path = repo_root / FEATURE_INDEX_REL
    map_path = repo_root / CODEBASE_MAP_REL
    if not index_path.is_file():
        findings.append(f"missing {FEATURE_INDEX_REL.as_posix()} (fail-closed)")
    if not map_path.is_file():
        findings.append(f"missing {CODEBASE_MAP_REL.as_posix()} (fail-closed)")
    if findings:
        return findings

    try:
        generator = _load_generator(repo_root)
        # The generator's warm-import hook resolves the boot-time import cycle.
        warm = getattr(generator, "_warm_import_graph", None)
        if callable(warm):
            warm()
        build_index = generator.build_feature_index  # type: ignore[attr-defined]
        build_map = generator.build_codebase_map  # type: ignore[attr-defined]
        expected_index = build_index().model_dump(mode="json")
        expected_map = {"modules": build_map()}
    except Exception as exc:
        findings.append(f"generator failed: {exc}")
        return findings

    try:
        committed_index_raw = _load_json(index_path)
        committed_map_raw = _load_json(map_path)
    except ValueError as exc:
        findings.append(f"failed to parse committed JSON: {exc}")
        return findings
    if not isinstance(committed_index_raw, dict):
        findings.append(
            f"{FEATURE_INDEX_REL.as_posix()} is not a JSON object (corrupt)"
        )
        return findings
    if not isinstance(committed_map_raw, dict):
        findings.append(f"{CODEBASE_MAP_REL.as_posix()} is not a JSON object (corrupt)")
        return findings

    if _strip_generated_at(committed_index_raw) != _strip_generated_at(expected_index):
        findings.append(
            f"{FEATURE_INDEX_REL.as_posix()} is stale; regenerate via "
            "`uv run python scripts/generate_feature_index.py`"
        )
    if committed_map_raw != expected_map:
        findings.append(
            f"{CODEBASE_MAP_REL.as_posix()} is stale; regenerate via "
            "`uv run python scripts/generate_feature_index.py`"
        )
    return findings


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assert the AI-navigation index is up to date."
    )
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT_DEFAULT)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on freshness, 1 otherwise."""
    args = _build_arg_parser().parse_args(argv)
    findings = check(repo_root=args.repo_root.resolve())
    if not findings:
        return 0
    print("Feature-index freshness findings:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
