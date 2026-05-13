#!/usr/bin/env python3
"""Pre-push + CI gate: ``data/runtime_stats.yaml`` must be fresh.

Fails when EITHER:

* any ``stats.<name>.display`` differs from a freshly-fetched value
  (drift detection: the user-facing string that appears in docs via
  ``<!--RS:NAME-->`` markers has changed at the source but the
  committed YAML still carries the prior value), OR
* committed ``last_generated_utc`` is older than
  :data:`_STALE_AFTER_DAYS` (safety net: nobody has run the generator
  and committed the result in too long, even if no individual stat
  appears to drift).

Reuses the fetcher inventory in ``scripts/generate_runtime_stats.py``;
the gate never writes the YAML, only reads it.

A fetcher raising :class:`_StatFetchError` is treated as "unable to
check" and does NOT count as drift; the gate emits a structured note
to stderr and continues. This mirrors the generator's offline-tolerant
fallback so a transient network failure does not red-light CI.

Exit codes
----------
* ``0`` -- clean (every checked stat matches and age is within ceiling).
* ``1`` -- any drift, age over the ceiling, or unreadable / malformed YAML.

Flags
-----
* ``--skip-network`` -- bypass fetchers that shell out to ``gh`` or
  ``pytest --collect-only`` (``tests``, ``version``, ``mem0_stars``).
  Pre-push uses this so developers without a ``gh`` token are not
  gated; CI runs without the flag to perform the full check.
"""

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_YAML_FILE: Path = REPO_ROOT / "data" / "runtime_stats.yaml"

# Drift safety net: nobody has run the generator in this many days.
# ``last_generated_utc`` is an attempt timestamp (advances on every
# generator run regardless of fetcher outcomes), so this age check is
# orthogonal to the per-stat drift check below.
_STALE_AFTER_DAYS: Final[int] = 14

# Fetchers whose source call is a subprocess to ``gh`` or
# ``pytest --collect-only``. Skipped under ``--skip-network`` so
# developers can pre-push without a configured ``gh`` token; CI runs
# without the flag and covers every fetcher.
_NETWORK_STATS: Final[frozenset[str]] = frozenset({"tests", "mem0_stars"})

_GENERATOR_PATH: Final[Path] = REPO_ROOT / "scripts" / "generate_runtime_stats.py"

_REMEDIATION: Final[str] = "Run: uv run python scripts/generate_runtime_stats.py"


def _load_generator() -> ModuleType:
    """Import ``scripts/generate_runtime_stats.py`` to reuse ``_FETCHERS``."""
    spec = importlib.util.spec_from_file_location(
        "generate_runtime_stats", _GENERATOR_PATH
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


# Tests patch this attribute to point at their own loaded generator
# instance so monkeypatched ``_FETCHERS`` are honoured. The default
# value is ``None``; ``main()`` resolves it via ``_ensure_generator()``
# so an import-time failure surfaces as a clean exit 1 instead of an
# unhandled traceback before ``argparse`` even runs.
GENERATOR_MODULE: ModuleType | None = None


def _ensure_generator() -> ModuleType:
    """Return the cached generator module, importing on first call."""
    global GENERATOR_MODULE  # noqa: PLW0603
    if GENERATOR_MODULE is None:
        GENERATOR_MODULE = _load_generator()
    return GENERATOR_MODULE


def _load_committed() -> dict[str, Any]:
    """Read committed YAML; raise ``RuntimeError`` on missing / corrupt input."""
    if not _YAML_FILE.is_file():
        msg = f"{_YAML_FILE} does not exist; cannot check freshness"
        raise RuntimeError(msg)
    try:
        text = _YAML_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"could not read {_YAML_FILE}: {type(exc).__name__}"
        raise RuntimeError(msg) from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"could not parse {_YAML_FILE} as YAML: {exc}"
        raise RuntimeError(msg) from exc
    if loaded is None:
        msg = f"{_YAML_FILE} is empty; cannot check freshness"
        raise RuntimeError(msg)
    if not isinstance(loaded, dict):
        msg = f"{_YAML_FILE} root is not a mapping; got {type(loaded).__name__}"
        raise TypeError(msg)
    return loaded


def _parse_iso_utc(value: str) -> dt.datetime:
    """Parse an ISO-8601 UTC timestamp produced by the generator."""
    normalised = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        msg = f"timestamp lacks tzinfo: {value!r}"
        raise ValueError(msg)
    return parsed.astimezone(dt.UTC)


def _check_age(committed: dict[str, Any]) -> list[str]:
    """Return age-violation lines (empty if fresh)."""
    raw_ts = committed.get("last_generated_utc")
    if not isinstance(raw_ts, str):
        return [
            f"{_YAML_FILE.name}: 'last_generated_utc' missing or non-string.",
            _REMEDIATION,
        ]
    try:
        parsed = _parse_iso_utc(raw_ts)
    except (ValueError, TypeError, OSError) as exc:
        return [
            f"{_YAML_FILE.name}: 'last_generated_utc' invalid ({exc}).",
            _REMEDIATION,
        ]
    age_days = (dt.datetime.now(dt.UTC) - parsed).days
    if age_days > _STALE_AFTER_DAYS:
        return [
            f"data/runtime_stats.yaml last regenerated {raw_ts[:10]} "
            f"({age_days}d > {_STALE_AFTER_DAYS}d).",
            _REMEDIATION,
        ]
    return []


def _check_drift(
    committed: dict[str, Any],
    gen_mod: ModuleType,
    *,
    skip_network: bool,
) -> list[str]:
    """Return drift lines (empty if every checked stat matches)."""
    stats_block = committed.get("stats", {})
    if not isinstance(stats_block, dict):
        return [
            f"{_YAML_FILE.name}: 'stats' is not a mapping; cannot check drift.",
            _REMEDIATION,
        ]
    stat_fetch_error = gen_mod._StatFetchError  # noqa: SLF001
    lines: list[str] = []
    for name, fetcher in gen_mod._FETCHERS.items():  # noqa: SLF001
        if skip_network and name in _NETWORK_STATS:
            continue
        try:
            entry = fetcher()
        except stat_fetch_error as exc:
            # Expected offline-tolerant skip.
            print(
                f"note: skipping drift check for {name} (fetch failed: {exc.reason})",
                file=sys.stderr,
            )
            continue
        except MemoryError, RecursionError:
            # Programming errors propagate per the project's async / error
            # convention; do not silently absorb resource exhaustion.
            raise
        except Exception as exc:
            # An unexpected fetcher failure (KeyError, ConnectionError, ...)
            # for one stat must not abort drift detection for the others.
            print(
                f"note: skipping drift check for {name} "
                f"(unexpected error: {type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            continue
        committed_entry = stats_block.get(name)
        if not isinstance(committed_entry, dict):
            lines.append(f"stats.{name} missing from committed YAML.")
            continue
        committed_display = committed_entry.get("display")
        actual_display = entry.get("display")
        if committed_display != actual_display:
            lines.append(
                f"stats.{name}.display drift: "
                f"committed={committed_display!r} actual={actual_display!r}"
            )
    if lines:
        lines.append(_REMEDIATION)
    return lines


def main(argv: list[str] | None = None) -> int:
    """Run the freshness check; return shell exit code."""
    parser = argparse.ArgumentParser(
        description=(
            "Fail the build if data/runtime_stats.yaml has drifted or is "
            "older than the staleness ceiling."
        )
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help=(
            "Skip network-backed fetchers (tests, version, mem0_stars). "
            "Used by pre-push so developers without a configured gh token "
            "are not blocked."
        ),
    )
    args = parser.parse_args(argv)

    try:
        committed = _load_committed()
    except (RuntimeError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        gen_mod = _ensure_generator()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failures: list[str] = []
    failures.extend(_check_age(committed))
    failures.extend(_check_drift(committed, gen_mod, skip_network=args.skip_network))

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    suffix = " (network-backed fetchers skipped)" if args.skip_network else ""
    print(
        f"OK: {_YAML_FILE.name} matches every fetcher and is within "
        f"{_STALE_AFTER_DAYS}d{suffix}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
