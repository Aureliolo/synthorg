#!/usr/bin/env python3
"""Generate the committed architecture-metrics baseline.

Writes ``data/architecture_report.json`` from the live import graph and
source tree: per-module fan-in (hubs), budget pressure (files near their
tier cap), and LCOM4 for large service classes. The companion gate
``scripts/check_architecture_drift.py`` recomputes the same metrics on
pre-push and fails on a regression past this baseline. The report is a
committed artifact regenerated on demand / in CI, not written by the
gate (so pre-push never dirties the tree), mirroring
``data/feature_index.json``.

Usage::

    uv run python scripts/architecture_report.py
    uv run python scripts/architecture_report.py --check
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_REPORT_REL = Path("data") / "architecture_report.json"

_DESCRIPTION = (
    "Architecture-metrics baseline for the drift gate "
    "(scripts/check_architecture_drift.py). fan_in: direct-importer counts "
    "for hub modules (>= 20). budget_pressure: source files within 20% of "
    "their module-size tier cap. lcom: LCOM4 for service-tier classes "
    ">= 400 LOC (1 = cohesive). Regenerate with: "
    "uv run python scripts/architecture_report.py"
)


def _load_lib() -> ModuleType:
    lib_path = Path(__file__).resolve().parent / "_architecture_lib.py"
    spec = importlib.util.spec_from_file_location("_architecture_lib", lib_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {lib_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LIB: Any = cast("Any", _load_lib())


def render(project_root: Path) -> str:
    """Return the deterministic JSON body for the report.

    Returns:
        Sorted-key, 2-space-indented JSON with a trailing newline.
    """
    payload = {"description": _DESCRIPTION, **_LIB.build_report(project_root)}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` on success; ``1`` in ``--check`` mode when the committed
        report is stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=_REPO_ROOT_DEFAULT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed report is up to date; do not write.",
    )
    args = parser.parse_args(argv)
    project_root: Path = args.project_root.resolve()
    report_path = project_root / _REPORT_REL
    body = render(project_root)
    if args.check:
        current = (
            report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        )
        if current != body:
            print(
                "data/architecture_report.json is stale; regenerate with "
                "`uv run python scripts/architecture_report.py`.",
                file=sys.stderr,
            )
            return 1
        return 0
    report_path.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
