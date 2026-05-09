#!/usr/bin/env python3
r"""Validate CodeQL pack-fixture SARIF outputs against expected.json.

Run by ``.github/workflows/codeql-pack-validate.yml``. Reads the manifest at
``.github/codeql/fixtures/expected.json``, locates the corresponding SARIF
result for each fixture under ``--results-dir``, and asserts:

* ``must_not_fire``: no alert with the listed ``ruleId`` may appear anywhere
  in the fixture's ``scan_paths``. A hit means the pack is under-modelling
  a sanitiser.
* ``must_fire``: at least one alert with each listed ``ruleId`` must appear
  in the fixture's ``scan_paths``. A miss means the pack is over-
  suppressing genuine leaks.
* ``must_not_fire_at`` / ``must_fire_at``: same as above but scoped to a
  named function (Go fixtures use this so negative + positive cases share
  one source-root and SARIF output).

Exits 0 on success, 1 on any assertion failure (with a diff-style report).

Usage:

    uv run python scripts/check_codeql_fixtures.py \\
        --expected .github/codeql/fixtures/expected.json \\
        --results-dir .github/codeql/fixtures/results
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

SUCCESS_EXIT_CODE = 0
FAILURE_EXIT_CODE = 1
USAGE_EXIT_CODE = 2


@dataclasses.dataclass(frozen=True)
class SarifAlert:
    """A single SARIF result distilled to the fields we assert on."""

    rule_id: str
    file: str
    start_line: int
    function: str | None


@dataclasses.dataclass(frozen=True)
class FixtureExpectation:
    """Per-fixture assertions read from expected.json."""

    name: str
    language: str
    scan_paths: tuple[str, ...]
    must_not_fire: tuple[str, ...]
    must_fire: tuple[str, ...]
    must_not_fire_at: tuple[tuple[str, str], ...]
    must_fire_at: tuple[tuple[str, str], ...]


def _parse_expectation(raw: dict[str, Any]) -> FixtureExpectation:
    """Build a FixtureExpectation from a single manifest entry."""
    return FixtureExpectation(
        name=raw["name"],
        language=raw["language"],
        scan_paths=tuple(raw.get("scan_paths", ())),
        must_not_fire=tuple(raw.get("must_not_fire", ())),
        must_fire=tuple(raw.get("must_fire", ())),
        must_not_fire_at=tuple(
            (entry["rule"], entry["function"])
            for entry in raw.get("must_not_fire_at", ())
        ),
        must_fire_at=tuple(
            (entry["rule"], entry["function"]) for entry in raw.get("must_fire_at", ())
        ),
    )


def _extract_alerts(sarif: dict[str, Any]) -> list[SarifAlert]:
    """Distil the SARIF envelope into a flat list of alerts.

    SARIF runs[*].results[*].locations[*].physicalLocation carries the
    file path; .logicalLocations[*].name carries the enclosing function
    when CodeQL emits it (Go always does; Python sometimes).
    """
    alerts: list[SarifAlert] = []
    for run in sarif.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId") or ""
            for location in result.get("locations", []):
                physical = location.get("physicalLocation", {})
                artifact = physical.get("artifactLocation", {})
                region = physical.get("region", {})
                file_uri = artifact.get("uri") or ""
                start_line = int(region.get("startLine", 0))
                func: str | None = None
                for logical in location.get("logicalLocations", []):
                    if logical.get("kind") == "function":
                        func = logical.get("name")
                        break
                alerts.append(
                    SarifAlert(
                        rule_id=rule_id,
                        file=file_uri,
                        start_line=start_line,
                        function=func,
                    )
                )
    return alerts


def _matches_scan_path(alert: SarifAlert, scan_paths: tuple[str, ...]) -> bool:
    """Return True if the alert's file is under any scan_path prefix.

    ``scan_paths`` is empty for whole-source-root fixtures; in that case
    every alert in the SARIF counts.
    """
    if not scan_paths:
        return True
    return any(
        alert.file == prefix or alert.file.startswith(prefix.rstrip("/") + "/")
        for prefix in scan_paths
    )


def _check_fixture(
    expectation: FixtureExpectation,
    alerts: list[SarifAlert],
) -> list[str]:
    """Return a list of failure messages; empty list = pass."""
    failures: list[str] = []
    in_scope = [a for a in alerts if _matches_scan_path(a, expectation.scan_paths)]

    for rule in expectation.must_not_fire:
        hits = [a for a in in_scope if a.rule_id == rule]
        if hits:
            sample = hits[0]
            failures.append(
                f"  must_not_fire violation: {rule} fired at "
                f"{sample.file}:{sample.start_line} "
                f"(pack is under-modelling a sanitiser)"
            )

    for rule in expectation.must_fire:
        hits = [a for a in in_scope if a.rule_id == rule]
        if not hits:
            failures.append(
                f"  must_fire miss: {rule} did NOT fire in "
                f"{','.join(expectation.scan_paths) or '<source-root>'} "
                f"(pack is over-suppressing genuine leaks)"
            )

    for rule, func in expectation.must_not_fire_at:
        hits = [a for a in in_scope if a.rule_id == rule and a.function == func]
        if hits:
            sample = hits[0]
            failures.append(
                f"  must_not_fire_at violation: {rule} fired in "
                f"function {func} at {sample.file}:{sample.start_line}"
            )

    for rule, func in expectation.must_fire_at:
        hits = [a for a in in_scope if a.rule_id == rule and a.function == func]
        if not hits:
            failures.append(
                f"  must_fire_at miss: {rule} did NOT fire in function {func}"
            )

    return failures


def _load_sarif(results_dir: Path, fixture_name: str) -> dict[str, Any] | None:
    """Locate ``<results_dir>/<fixture_name>.sarif`` and parse it."""
    path = results_dir / f"{fixture_name}.sarif"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    """Drive validation across every fixture in expected.json."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument(
        "--expected",
        type=Path,
        default=Path(".github/codeql/fixtures/expected.json"),
        help="Path to the expectations manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(".github/codeql/fixtures/results"),
        help="Directory containing <fixture-name>.sarif files (default: %(default)s)",
    )
    args = parser.parse_args()

    if not args.expected.exists():
        print(
            f"error: expectations manifest not found: {args.expected}", file=sys.stderr
        )
        return USAGE_EXIT_CODE

    manifest = json.loads(args.expected.read_text(encoding="utf-8"))
    fixtures = manifest.get("fixtures", [])
    if not fixtures:
        print("error: no fixtures declared in expectations manifest", file=sys.stderr)
        return USAGE_EXIT_CODE

    total_failures: list[str] = []
    for raw in fixtures:
        expectation = _parse_expectation(raw)
        sarif = _load_sarif(args.results_dir, expectation.name)
        if sarif is None:
            total_failures.append(
                f"[{expectation.name}] missing SARIF: "
                f"{args.results_dir / (expectation.name + '.sarif')}"
            )
            continue

        alerts = _extract_alerts(sarif)
        failures = _check_fixture(expectation, alerts)
        if failures:
            total_failures.append(f"[{expectation.name}] failed:")
            total_failures.extend(failures)
        else:
            print(f"[{expectation.name}] OK ({len(alerts)} total alerts in SARIF)")

    if total_failures:
        print(file=sys.stderr)
        print("CodeQL pack validation FAILED:", file=sys.stderr)
        for line in total_failures:
            print(line, file=sys.stderr)
        return FAILURE_EXIT_CODE

    print()
    print(f"All {len(fixtures)} fixtures passed.")
    return SUCCESS_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
