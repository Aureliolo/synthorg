#!/usr/bin/env python3
"""Generate ``data/runtime_stats.yaml`` from authoritative build-time sources.

Sources (best-effort; failures keep the previously-stored value):

* ``tests``                 -- ``uv run python -m pytest --collect-only -q``
* ``version``               -- ``gh release list --limit 1 --json tagName``
* ``mem0_stars``            -- ``gh api repos/mem0ai/mem0 --jq .stargazers_count``
* ``providers_curated``     -- ``len(synthorg.providers.presets.list_presets())``
* ``providers_via_litellm`` -- ``len(litellm.model_cost)``
* ``subagents``             -- ``glob .claude/agents/*.md``

Run before ``zensical build``::

    uv run python scripts/generate_runtime_stats.py

The script is offline-tolerant for *fetcher failures*: when a source call
fails (network down, ``gh`` unauthenticated, ``litellm`` not importable,
shallow checkout without ``.git``) it logs a warning to stderr and
preserves the prior value from the existing YAML. Every run rewrites the
schema header, timestamp, and generator revision regardless.

A *corrupted* existing YAML is treated differently: it is a hard error
(exit 1) so a malformed snapshot cannot silently drop every prior value.
The fix is to restore the file from git history before re-running.

Numeric thresholds are named module constants -- never magic literals.
"""

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypedDict

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_FILE: Path = REPO_ROOT / "data" / "runtime_stats.yaml"

_SCHEMA_VERSION: Final[int] = 1

# Round test count to nearest 1,000 so headline prose stays stable
# across normal CI variance (a few new tests per PR).
_TESTS_ROUND_TO: Final[int] = 1000
# Round star count to nearest 1,000 so the rendered "Nk+" string is stable
# across normal day-to-day star drift.
_STARS_ROUND_TO: Final[int] = 1000
# Round LiteLLM model-cost length to nearest 100 so the rendered "N+"
# stays stable across minor LiteLLM dependency bumps.
_LITELLM_ROUND_TO: Final[int] = 100

_GH_TIMEOUT_SECONDS: Final[int] = 30
_PYTEST_TIMEOUT_SECONDS: Final[int] = 120
_GIT_TIMEOUT_SECONDS: Final[int] = 5

_PYTEST_SUMMARY_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+)\s+(?:tests?|items?)\s+collected", re.MULTILINE
)

_SOURCES: Final[dict[str, str]] = {
    "tests": "uv run python -m pytest --collect-only -q",
    "version": "gh release list --limit 1 --json tagName",
    "mem0_stars": "gh api repos/mem0ai/mem0 --jq .stargazers_count",
    "providers_curated": "synthorg.providers.presets.list_presets",
    "providers_via_litellm": "len(litellm.model_cost)",
    "subagents": "glob .claude/agents/*.md",
}


class StatEntry(TypedDict, total=False):
    """A single stat entry from ``data/runtime_stats.yaml``.

    ``display`` is required (the injector substitutes it into docs).
    ``raw`` and ``rounded`` are informational and may be omitted on
    stats that have no rounding step (e.g. ``version`` carries only a
    string tag).
    """

    raw: int | str
    rounded: int
    display: str


class _StatFetchError(Exception):
    """A single stat could not be fetched; caller preserves prior value.

    Carries structured context so log aggregation can route by stat
    name and failure reason without parsing the message string.
    """

    def __init__(self, stat_name: str, source: str, reason: str) -> None:
        self.stat_name = stat_name
        self.source = source
        self.reason = reason
        super().__init__(f"{stat_name} via {source}: {reason}")


def _round_floor(value: int, step: int) -> int:
    """Floor *value* to the nearest multiple of *step*."""
    if step <= 0:
        msg = f"step must be positive, got {step}"
        raise ValueError(msg)
    return (value // step) * step


def _format_thousands_plus(value: int) -> str:
    """Render *value* with US-style thousands separators and a ``+``.

    Used for the ``tests`` stat -- ``"17,000+"`` style.
    """
    return f"{value:,}+"


def _format_k_plus(value: int) -> str:
    """Render *value* compactly as ``Nk+``.

    Used for the ``mem0_stars`` stat -- ``"54k+"`` style.
    """
    return f"{value // _STARS_ROUND_TO}k+"


def _run(
    cmd: list[str], timeout: int, *, stat_name: str, source: str
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* capturing stdout as text, raising _StatFetchError on failure."""
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError as exc:
        raise _StatFetchError(
            stat_name, source, f"{cmd[0]} exited with code {exc.returncode}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _StatFetchError(
            stat_name, source, f"{cmd[0]} timed out after {timeout}s"
        ) from exc
    except FileNotFoundError as exc:
        raise _StatFetchError(stat_name, source, f"{cmd[0]} not found on PATH") from exc


def _fetch_tests() -> StatEntry:
    """Count tests via ``uv run python -m pytest --collect-only -q``."""
    name = "tests"
    source = _SOURCES[name]
    result = _run(
        ["uv", "run", "python", "-m", "pytest", "--collect-only", "-q"],
        timeout=_PYTEST_TIMEOUT_SECONDS,
        stat_name=name,
        source=source,
    )
    match = _PYTEST_SUMMARY_RE.search(result.stdout)
    if match is None:
        raise _StatFetchError(
            name, source, "pytest stdout missing 'N tests collected' summary"
        )
    raw = int(match.group(1))
    rounded = _round_floor(raw, _TESTS_ROUND_TO)
    return {
        "raw": raw,
        "rounded": rounded,
        "display": _format_thousands_plus(rounded),
    }


def _fetch_version() -> StatEntry:
    """Read the latest release tag via ``gh release list --limit 1``."""
    name = "version"
    source = _SOURCES[name]
    result = _run(
        ["gh", "release", "list", "--limit", "1", "--json", "tagName"],
        timeout=_GH_TIMEOUT_SECONDS,
        stat_name=name,
        source=source,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _StatFetchError(
            name, source, f"gh release list returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise _StatFetchError(name, source, "gh release list returned no entries")
    tag = payload[0].get("tagName")
    if not isinstance(tag, str) or not tag:
        raise _StatFetchError(name, source, "gh release list entry missing 'tagName'")
    return {"raw": tag, "display": tag}


def _fetch_mem0_stars() -> StatEntry:
    """Read Mem0's star count via ``gh api`` and round down to nearest 1000."""
    name = "mem0_stars"
    source = _SOURCES[name]
    result = _run(
        ["gh", "api", "repos/mem0ai/mem0", "--jq", ".stargazers_count"],
        timeout=_GH_TIMEOUT_SECONDS,
        stat_name=name,
        source=source,
    )
    raw_str = result.stdout.strip()
    try:
        raw = int(raw_str)
    except ValueError as exc:
        raise _StatFetchError(
            name, source, f"stargazers_count returned non-integer: {raw_str!r}"
        ) from exc
    rounded = _round_floor(raw, _STARS_ROUND_TO)
    return {
        "raw": raw,
        "rounded": rounded,
        "display": _format_k_plus(rounded),
    }


def _fetch_providers_curated() -> StatEntry:
    """Count curated provider presets via ``synthorg.providers.presets``."""
    name = "providers_curated"
    source = _SOURCES[name]
    try:
        from synthorg.providers.presets import list_presets
    except ImportError as exc:
        raise _StatFetchError(
            name, source, f"could not import synthorg.providers.presets: {exc}"
        ) from exc
    raw = len(list_presets())
    return {"raw": raw, "display": str(raw)}


def _fetch_providers_via_litellm() -> StatEntry:
    """Count LiteLLM-known providers via ``len(litellm.model_cost)``.

    LiteLLM tracks several hundred model entries in ``model_cost``; the
    final figure is rounded down to nearest ``_LITELLM_ROUND_TO`` (100)
    and rendered as ``"N+"`` so headline prose stays stable across
    minor LiteLLM bumps.
    """
    name = "providers_via_litellm"
    source = _SOURCES[name]
    try:
        import litellm
    except ImportError as exc:
        raise _StatFetchError(name, source, f"could not import litellm: {exc}") from exc
    raw = len(litellm.model_cost)
    rounded = _round_floor(raw, _LITELLM_ROUND_TO)
    return {"raw": raw, "display": f"{rounded}+"}


def _fetch_subagents() -> StatEntry:
    """Count `.claude/agents/*.md` markdown files."""
    name = "subagents"
    source = _SOURCES[name]
    agents_dir = REPO_ROOT / ".claude" / "agents"
    if not agents_dir.is_dir():
        raise _StatFetchError(
            name, source, f".claude/agents/ not found at {agents_dir}"
        )
    count = sum(1 for _ in agents_dir.glob("*.md"))
    return {"raw": count, "display": str(count)}


_FETCHERS: dict[str, Callable[[], StatEntry]] = {
    "tests": _fetch_tests,
    "version": _fetch_version,
    "mem0_stars": _fetch_mem0_stars,
    "providers_curated": _fetch_providers_curated,
    "providers_via_litellm": _fetch_providers_via_litellm,
    "subagents": _fetch_subagents,
}


def _validate_fetcher_source_parity() -> None:
    """Fail-fast at startup if `_FETCHERS` and `_SOURCES` keys diverge.

    A typo in either declaration would otherwise silently skip a stat
    or emit a stat with no source-of-truth annotation.
    """
    fetcher_names = set(_FETCHERS)
    source_names = set(_SOURCES)
    if fetcher_names != source_names:
        only_fetchers = sorted(fetcher_names - source_names)
        only_sources = sorted(source_names - fetcher_names)
        msg = (
            "Generator config drift: _FETCHERS and _SOURCES keys disagree. "
            f"Only in _FETCHERS: {only_fetchers}. "
            f"Only in _SOURCES: {only_sources}."
        )
        raise RuntimeError(msg)


def _load_existing() -> dict[str, Any]:
    """Return existing YAML contents.

    Returns an empty dict when the file does not exist (first-ever
    run). Raises ``RuntimeError`` when the file exists but cannot be
    parsed -- a corrupted snapshot must not silently drop every prior
    stat. Operators recover by restoring the file from git history.
    """
    if not _OUT_FILE.is_file():
        return {}
    try:
        text = _OUT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"could not read existing {_OUT_FILE}: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = (
            f"existing {_OUT_FILE} is not valid YAML; restore from git "
            f"or delete and re-run. Parse error: {exc}"
        )
        raise RuntimeError(msg) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        msg = f"existing {_OUT_FILE} root is not a mapping; got {type(loaded).__name__}"
        raise TypeError(msg)
    stats_block = loaded.get("stats")
    if stats_block is not None and not isinstance(stats_block, dict):
        msg = (
            f"existing {_OUT_FILE} field 'stats' is not a mapping; "
            f"got {type(stats_block).__name__}"
        )
        raise TypeError(msg)
    return loaded


def _validate_stat_entry(name: str, entry: StatEntry) -> None:
    """Ensure a fetcher result carries the required ``display`` field.

    The injector trusts ``stats[name]["display"]`` to be a non-empty
    string. Validating at write-time catches a buggy fetcher before it
    lands a malformed entry on disk.
    """
    display = entry.get("display")
    if not isinstance(display, str) or not display:
        msg = (
            f"fetcher for stat {name!r} returned entry missing a "
            f"non-empty 'display' string field; got {entry!r}"
        )
        raise RuntimeError(msg)


def _git_head_unknown(detail: str) -> str:
    """Print a stderr warning explaining why the git head is unavailable."""
    print(
        f"warning: {detail}; generator_revision will be 'unknown'",
        file=sys.stderr,
    )
    return "unknown"


def _git_head() -> str:
    """Return the short git HEAD sha or the literal ``'unknown'``.

    Each fallback path emits a distinct stderr warning so the operator
    can tell "git not on PATH" from "shallow clone with no HEAD" from
    "git rev-parse timed out". The audit trail in ``generator_revision``
    is best-effort but the failure mode is always visible.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError as exc:
        return _git_head_unknown(f"git rev-parse exited with code {exc.returncode}")
    except subprocess.TimeoutExpired:
        return _git_head_unknown(
            f"git rev-parse timed out after {_GIT_TIMEOUT_SECONDS}s"
        )
    except FileNotFoundError:
        return _git_head_unknown("git not found on PATH")
    head = result.stdout.strip()
    if not head:
        return _git_head_unknown("git rev-parse returned empty stdout")
    return head


def _now_utc_iso() -> str:
    """Return the current UTC instant in ISO-8601 with seconds precision."""
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main() -> int:
    """Refresh data/runtime_stats.yaml; return shell exit code."""
    _validate_fetcher_source_parity()
    try:
        existing = _load_existing()
    except (RuntimeError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    new_stats: dict[str, Any] = dict(existing.get("stats", {}))
    for name, fetcher in _FETCHERS.items():
        try:
            entry = fetcher()
        except _StatFetchError as exc:
            had_prior = name in new_stats
            print(
                f"warning: runtime_stats fetch_failed stat={exc.stat_name} "
                f"source={exc.source!r} reason={exc.reason!r} "
                f"keeping_prior_value={had_prior}",
                file=sys.stderr,
            )
            # Keep prior value; do NOT delete or zero out.
            continue
        try:
            _validate_stat_entry(name, entry)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        new_stats[name] = entry

    # ``last_generated_utc`` records the *attempt* timestamp, not a
    # success marker -- it advances on every run regardless of which
    # fetchers succeeded. To detect staleness, compare individual stat
    # values across runs rather than relying on this field.
    output: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "last_generated_utc": _now_utc_iso(),
        "generator_revision": _git_head(),
        "stats": new_stats,
        "sources": _SOURCES,
    }

    _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_FILE.write_text(
        yaml.safe_dump(output, sort_keys=False),
        encoding="utf-8",
    )
    try:
        rel = _OUT_FILE.relative_to(REPO_ROOT)
        display_path = rel.as_posix()
    except ValueError:
        display_path = str(_OUT_FILE)
    print(f"wrote {display_path} ({len(new_stats)} stat(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
