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

The script is offline-tolerant: when a source call fails (network down,
``gh`` unauthenticated, ``litellm`` not importable, shallow checkout
without ``.git``) it logs a warning to stderr and preserves the prior
value from the existing YAML. Every run rewrites the schema header,
timestamp, and generator revision regardless.

Numeric thresholds are named module constants -- never magic literals.
"""

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_FILE: Path = REPO_ROOT / "data" / "runtime_stats.yaml"

_SCHEMA_VERSION: Final[int] = 1

_TESTS_ROUND_TO: Final[int] = 1000
_STARS_ROUND_TO: Final[int] = 1000
_LITELLM_ROUND_TO: Final[int] = 100

_GH_TIMEOUT_SECONDS: Final[int] = 30
_PYTEST_TIMEOUT_SECONDS: Final[int] = 120
_GIT_TIMEOUT_SECONDS: Final[int] = 5

_PYTEST_SUMMARY_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+)\s+tests?\s+collected", re.MULTILINE
)

_SOURCES: Final[dict[str, str]] = {
    "tests": "uv run python -m pytest --collect-only -q",
    "version": "gh release list --limit 1 --json tagName",
    "mem0_stars": "gh api repos/mem0ai/mem0 --jq .stargazers_count",
    "providers_curated": "synthorg.providers.presets.list_presets",
    "providers_via_litellm": "len(litellm.model_cost)",
    "subagents": "glob .claude/agents/*.md",
}


class _StatFetchError(Exception):
    """A single stat could not be fetched; caller preserves prior value."""


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


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run *cmd* capturing stdout as text, raising _StatFetchError on failure."""
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        msg = f"{cmd[0]} exited with code {exc.returncode}"
        raise _StatFetchError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"{cmd[0]} timed out after {timeout}s"
        raise _StatFetchError(msg) from exc
    except FileNotFoundError as exc:
        msg = f"{cmd[0]} not found on PATH"
        raise _StatFetchError(msg) from exc


def _fetch_tests() -> dict[str, Any]:
    """Count tests via ``pytest --collect-only -q``."""
    result = _run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "--collect-only",
            "-q",
        ],
        timeout=_PYTEST_TIMEOUT_SECONDS,
    )
    match = _PYTEST_SUMMARY_RE.search(result.stdout)
    if match is None:
        msg = "pytest stdout missing 'N tests collected' summary line"
        raise _StatFetchError(msg)
    raw = int(match.group(1))
    rounded = _round_floor(raw, _TESTS_ROUND_TO)
    return {
        "raw": raw,
        "rounded": rounded,
        "display": _format_thousands_plus(rounded),
    }


def _fetch_version() -> dict[str, Any]:
    """Read the latest release tag via ``gh release list --limit 1``."""
    result = _run(
        ["gh", "release", "list", "--limit", "1", "--json", "tagName"],
        timeout=_GH_TIMEOUT_SECONDS,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"gh release list returned invalid JSON: {exc}"
        raise _StatFetchError(msg) from exc
    if not isinstance(payload, list) or not payload:
        msg = "gh release list returned no entries"
        raise _StatFetchError(msg)
    tag = payload[0].get("tagName")
    if not isinstance(tag, str) or not tag:
        msg = "gh release list entry missing 'tagName'"
        raise _StatFetchError(msg)
    return {"raw": tag, "display": tag}


def _fetch_mem0_stars() -> dict[str, Any]:
    """Read Mem0's star count via ``gh api`` and round down to nearest 1000."""
    result = _run(
        [
            "gh",
            "api",
            "repos/mem0ai/mem0",
            "--jq",
            ".stargazers_count",
        ],
        timeout=_GH_TIMEOUT_SECONDS,
    )
    raw_str = result.stdout.strip()
    try:
        raw = int(raw_str)
    except ValueError as exc:
        msg = f"gh api stargazers_count returned non-integer: {raw_str!r}"
        raise _StatFetchError(msg) from exc
    rounded = _round_floor(raw, _STARS_ROUND_TO)
    return {
        "raw": raw,
        "rounded": rounded,
        "display": _format_k_plus(rounded),
    }


def _fetch_providers_curated() -> dict[str, Any]:
    """Count curated provider presets via ``synthorg.providers.presets``."""
    try:
        from synthorg.providers.presets import list_presets
    except ImportError as exc:
        msg = f"could not import synthorg.providers.presets: {exc}"
        raise _StatFetchError(msg) from exc
    raw = len(list_presets())
    return {"raw": raw, "display": str(raw)}


def _fetch_providers_via_litellm() -> dict[str, Any]:
    """Count LiteLLM-known providers via ``len(litellm.model_cost)``.

    LiteLLM tracks ~hundreds of model entries in ``model_cost``; the
    final figure is rounded down to nearest 100 and rendered as
    ``"Nx100+"`` (e.g. ``"500+"``) so headline prose stays stable
    across minor LiteLLM bumps.
    """
    try:
        import litellm
    except ImportError as exc:
        msg = f"could not import litellm: {exc}"
        raise _StatFetchError(msg) from exc
    raw = len(litellm.model_cost)
    rounded = _round_floor(raw, _LITELLM_ROUND_TO)
    return {"raw": raw, "display": f"{rounded}+"}


def _fetch_subagents() -> dict[str, Any]:
    """Count `.claude/agents/*.md` markdown files."""
    agents_dir = REPO_ROOT / ".claude" / "agents"
    if not agents_dir.is_dir():
        msg = f".claude/agents/ not found at {agents_dir}"
        raise _StatFetchError(msg)
    count = sum(1 for _ in agents_dir.glob("*.md"))
    return {"raw": count, "display": str(count)}


_FETCHERS: dict[str, Callable[[], dict[str, Any]]] = {
    "tests": _fetch_tests,
    "version": _fetch_version,
    "mem0_stars": _fetch_mem0_stars,
    "providers_curated": _fetch_providers_curated,
    "providers_via_litellm": _fetch_providers_via_litellm,
    "subagents": _fetch_subagents,
}


def _load_existing() -> dict[str, Any]:
    """Return existing YAML contents (empty dict if missing or unreadable)."""
    if not _OUT_FILE.is_file():
        return {}
    try:
        loaded = yaml.safe_load(_OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(
            f"warning: could not read existing {_OUT_FILE.name}: {type(exc).__name__}",
            file=sys.stderr,
        )
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _git_head() -> str:
    """Return the short git HEAD sha or the literal ``'unknown'``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=REPO_ROOT,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return "unknown"
    return result.stdout.strip() or "unknown"


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
    existing = _load_existing()
    new_stats: dict[str, Any] = dict(existing.get("stats", {}))
    for name, fetcher in _FETCHERS.items():
        try:
            new_stats[name] = fetcher()
        except _StatFetchError as exc:
            print(
                f"warning: runtime_stats fetch_failed stat={name} "
                f"error_type=_StatFetchError error={str(exc)[:120]!r}",
                file=sys.stderr,
            )
            # Keep prior value; do NOT delete or zero out.

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
