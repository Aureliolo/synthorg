#!/usr/bin/env python3
"""Architecture-drift gate (pre-push + CI).

Recomputes the architecture metrics live and compares them to the
committed ``data/architecture_report.json`` baseline, failing on a
regression past threshold with structured remediation guidance. The gate
NEVER writes the report (a pre-push hook must not dirty the tree); the
report is a committed artifact regenerated on demand via
``scripts/architecture_report.py``.

A regression is one of:

* **fan-in**: a module's direct-importer count is at or above
  ``FAN_IN_FAIL_THRESHOLD`` (30) and exceeds its baseline by more than
  ``FAN_IN_DRIFT_TOLERANCE`` (a new hub, or a hub coupling materially
  harder than recorded).
* **budget pressure**: a source file newly enters the "within 20% of its
  tier cap" zone (a new file already close to needing a split).
* **LCOM4**: a large service class becomes less cohesive than recorded,
  or a new >= 400-LOC service class lands with LCOM4 >= 2.

Usage::

    uv run python scripts/check_architecture_drift.py
"""

import argparse
import json
import sys
from pathlib import Path
from typing import cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _architecture_lib import (  # type: ignore[import-not-found]
        FAN_IN_DRIFT_TOLERANCE,
        FAN_IN_FAIL_THRESHOLD,
        LCOM_COHESIVE_MAX,
        compute_budget_pressure,
        compute_fan_in,
        compute_lcom,
    )
else:
    from scripts._architecture_lib import (
        FAN_IN_DRIFT_TOLERANCE,
        FAN_IN_FAIL_THRESHOLD,
        LCOM_COHESIVE_MAX,
        compute_budget_pressure,
        compute_fan_in,
        compute_lcom,
    )

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_REPORT_REL = Path("data") / "architecture_report.json"


def check(*, project_root: Path, baseline: dict[str, object]) -> list[str]:
    """Return human-readable regression messages (empty when clean).

    Args:
        project_root: Resolved repo root.
        baseline: The parsed ``data/architecture_report.json`` payload.

    Returns:
        One message per regression, in deterministic order.
    """
    violations: list[str] = []

    base_fan = cast("dict[str, int]", baseline.get("fan_in", {}))
    live_fan = compute_fan_in(record_floor=0)
    for module, fan_in in sorted(live_fan.items()):
        if fan_in < FAN_IN_FAIL_THRESHOLD:
            continue
        recorded = base_fan.get(module, 0)
        if fan_in > recorded + FAN_IN_DRIFT_TOLERANCE:
            violations.append(
                f"fan-in: {module} is imported by {fan_in} modules "
                f"(baseline {recorded}); it is becoming a coupling hub. "
                f"Invert the dependency (depend on a protocol) or split the "
                f"module, or regenerate the baseline if this is intended."
            )

    base_bp = cast("dict[str, dict[str, object]]", baseline.get("budget_pressure", {}))
    live_bp = compute_budget_pressure(project_root)
    for path, info in sorted(live_bp.items()):
        if path not in base_bp:
            violations.append(
                f"budget-pressure: {path} is new and already at "
                f"{info['loc']}/{info['cap']} LOC ({info['ratio']:.0%} of the "
                f"{info['tier']} cap). Land it smaller or split it before it "
                f"breaches the module-size cap."
            )

    base_lcom = cast("dict[str, dict[str, int]]", baseline.get("lcom", {}))
    live_lcom = compute_lcom(project_root)
    for cls, info in sorted(live_lcom.items()):
        recorded_cls = base_lcom.get(cls)
        if recorded_cls is None:
            if info["lcom4"] > LCOM_COHESIVE_MAX:
                violations.append(
                    f"lcom: new large service class {cls} "
                    f"({info['loc']} LOC) has LCOM4 {info['lcom4']} (>= 2): it "
                    f"hosts unrelated responsibilities. Split it along the "
                    f"cohesion boundary."
                )
        elif info["lcom4"] > recorded_cls["lcom4"]:
            violations.append(
                f"lcom: {cls} cohesion regressed (LCOM4 "
                f"{recorded_cls['lcom4']} -> {info['lcom4']}); a new method "
                f"shares no state with the rest. Move it to a cohesive home "
                f"or regenerate the baseline if intended."
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when no regression; ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=_REPO_ROOT_DEFAULT)
    args = parser.parse_args(argv)
    project_root: Path = args.project_root.resolve()
    report_path = project_root / _REPORT_REL
    if not report_path.is_file():
        print(
            f"{_REPORT_REL.as_posix()} is missing; generate it with "
            f"`uv run python scripts/architecture_report.py`.",
            file=sys.stderr,
        )
        return 1
    baseline = json.loads(report_path.read_text(encoding="utf-8"))
    violations = check(project_root=project_root, baseline=baseline)
    if not violations:
        return 0
    for violation in violations:
        print(violation, file=sys.stderr)
    print(
        "\nArchitecture-drift gate failed. Each item is a regression past "
        "data/architecture_report.json. Fix the coupling/cohesion, or, if the "
        "change is intended, regenerate the baseline:\n"
        "  uv run python scripts/architecture_report.py\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
