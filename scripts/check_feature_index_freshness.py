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


_MAP_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {"module", "kind", "loc_cap", "loc", "owning_feature"}
)


def _validate_codebase_map(
    modules: list[object],
    expected_index: dict[str, object],
) -> list[str]:
    """Validate the regenerated codebase map's structure + cross-consistency.

    ``codebase_map.json`` is no longer committed (it is a regenerated,
    gitignored navigation artefact), so there is nothing to byte-compare.
    Instead this asserts the generator produced a well-formed map and
    that it stays consistent with the still-tracked ``feature_index.json``:
    every ``owning_feature`` must name a feature present in the index.

    Returns:
        Findings; empty means the map is structurally sound and consistent.
    """
    findings: list[str] = []
    if not modules:
        findings.append(f"{CODEBASE_MAP_REL.as_posix()} regenerated empty (corrupt)")
        return findings
    features_raw = expected_index.get("features", [])
    features = features_raw if isinstance(features_raw, list) else []
    feature_names = {
        feature["name"]
        for feature in features
        if isinstance(feature, dict) and "name" in feature
    }
    for entry in modules:
        if not isinstance(entry, dict):
            findings.append(
                f"{CODEBASE_MAP_REL.as_posix()} contains non-object entry: {entry!r}"
            )
            continue
        missing = _MAP_ENTRY_KEYS - entry.keys()
        if missing:
            findings.append(
                f"{CODEBASE_MAP_REL.as_posix()} entry {entry.get('module')!r} "
                f"missing keys: {sorted(missing)}"
            )
            continue
        owning = entry["owning_feature"]
        if owning is not None and owning not in feature_names:
            findings.append(
                f"{CODEBASE_MAP_REL.as_posix()} entry {entry['module']!r} names "
                f"owning_feature {owning!r} absent from {FEATURE_INDEX_REL.as_posix()}"
            )
    return findings


def check(*, repo_root: Path) -> list[str]:
    """Return findings (empty == artefacts match the freshly-regenerated state).

    ``feature_index.json`` is the committed source of truth and is
    byte-compared against a fresh regeneration (modulo the ``generated_at``
    field, which moves every run). ``codebase_map.json`` is no longer
    committed -- it is a regenerated, gitignored navigation artefact
    (``scripts/generate_feature_index.py`` writes it on demand) -- so the
    gate rebuilds it in memory and validates its structure and
    cross-consistency with the still-tracked index rather than
    byte-comparing a committed file.

    Args:
        repo_root: Repository root containing ``data/`` and ``scripts/``.

    Returns:
        List of human-readable findings; empty list means the gate passes.
    """
    findings: list[str] = []
    index_path = repo_root / FEATURE_INDEX_REL
    if not index_path.is_file():
        findings.append(f"missing {FEATURE_INDEX_REL.as_posix()} (fail-closed)")
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
        expected_modules = build_map()
    except Exception as exc:
        findings.append(f"generator failed: {exc}")
        return findings

    try:
        committed_index_raw = _load_json(index_path)
    except ValueError as exc:
        findings.append(f"failed to parse committed JSON: {exc}")
        return findings
    if not isinstance(committed_index_raw, dict):
        findings.append(
            f"{FEATURE_INDEX_REL.as_posix()} is not a JSON object (corrupt)"
        )
        return findings

    committed_index_norm = _canonical_json(_strip_generated_at(committed_index_raw))
    expected_index_norm = _canonical_json(_strip_generated_at(expected_index))
    if committed_index_norm != expected_index_norm:
        findings.append(
            f"{FEATURE_INDEX_REL.as_posix()} is stale; regenerate via "
            "`uv run python scripts/generate_feature_index.py`"
        )

    findings.extend(_validate_codebase_map(expected_modules, expected_index))
    return findings


def _canonical_json(payload: object) -> str:
    """Return *payload* serialised to canonical bytes for byte-level diffing.

    Sorted keys and compact separators guarantee that two semantically-equal
    payloads produce identical byte strings, so freshness is enforced at the
    byte level regardless of formatter drift in the committed artefact.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


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
