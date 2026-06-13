#!/usr/bin/env python3
"""Generate ``data/runtime_stats.yaml`` from authoritative build-time sources.

Sources (best-effort; failures keep the previously-stored value):

* ``tests``                 -- ``uv run python -m pytest --collect-only -q``
* ``providers_curated``     -- ``len(synthorg.providers.presets.list_featured_presets())``
* ``providers_via_litellm`` -- ``len(litellm.models_by_provider)``
* ``subagents``             -- ``glob .claude/agents/*.md``
* ``convention_gates``      -- ``glob scripts/check_*.py``
* ``mcp_tools``             -- tool builders in ``meta/mcp/domains/*.py``
* ``mcp_domains``           -- non-underscore modules in ``meta/mcp/domains/``
* ``settings_namespaces``   -- ``SettingNamespace`` members in ``src/synthorg/settings/enums.py``

Run before ``zensical build``::

    uv run python scripts/generate_runtime_stats.py

The script is offline-tolerant for *fetcher failures*: when a source call
fails (``pytest`` collection error, ``litellm`` not importable,
shallow checkout without ``.git``) it logs a warning to stderr and
preserves the prior value from the existing YAML. Every run rewrites the
schema header, timestamp, and generator revision regardless.

A *corrupted* existing YAML is treated differently: it is a hard error
(exit 1) so a malformed snapshot cannot silently drop every prior value.
The fix is to restore the file from git history before re-running.

Numeric thresholds are named module constants -- never magic literals.
"""

import ast
import datetime as dt
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final, TypedDict

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_FILE: Path = REPO_ROOT / "data" / "runtime_stats.yaml"

_SCHEMA_VERSION: Final[int] = 1

# Round test count to nearest 1,000 so headline prose stays stable
# across normal CI variance (a few new tests per PR).
_TESTS_ROUND_TO: Final[int] = 1000
# Round the LiteLLM provider count down to the nearest 5 so the rendered
# "N+" stays stable across minor LiteLLM dependency bumps.
_LITELLM_PROVIDER_ROUND_TO: Final[int] = 5

_PYTEST_TIMEOUT_SECONDS: Final[int] = 120
_GIT_TIMEOUT_SECONDS: Final[int] = 5

_PYTEST_SUMMARY_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+)\s+(?:tests?|items?)\s+collected", re.MULTILINE
)

_SOURCES: Final[dict[str, str]] = {
    "tests": "uv run python -m pytest --collect-only -q",
    "providers_curated": "synthorg.providers.presets.list_featured_presets",
    "providers_via_litellm": "len(litellm.models_by_provider)",
    "subagents": "glob .claude/agents/*.md",
    "convention_gates": "glob scripts/check_*.py",
    "mcp_tools": "tool builders in src/synthorg/meta/mcp/domains/*.py",
    "mcp_domains": "non-underscore modules in src/synthorg/meta/mcp/domains/",
    "settings_namespaces": "SettingNamespace members in src/synthorg/settings/enums.py",
}


class StatEntry(TypedDict, total=False):
    """A single stat entry from ``data/runtime_stats.yaml``.

    ``display`` is required (the injector substitutes it into docs).
    ``raw`` and ``rounded`` are informational and may be omitted on
    stats that have no rounding step.
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


_PRESETS_PATH: Final[Path] = REPO_ROOT / "src" / "synthorg" / "providers" / "presets.py"
_FEATURED_TUPLE_NAME: Final[str] = "_FEATURED_PRESETS"


def _fetch_providers_curated() -> StatEntry:
    """Count hand-curated featured provider presets.

    Tracks ``_FEATURED_PRESETS`` (the hand-curated entries with brand
    logo, vetted description, and prefilled model defaults), not the
    full ``list_presets()`` surface which auto-derives an entry for
    every LiteLLM chat namespace. The public claim is the featured
    count because that is what surfaces in the setup wizard's primary
    grid.

    Uses an ``ast`` walk of ``presets.py`` rather than an ``import``:
    the providers package pulls in the budget tracker which has its own
    initialisation chain, and a one-shot script must not pay that cost
    (or fail when the chain has unrelated circular-import drift). The
    tuple literal is the authoritative source either way.
    """
    name = "providers_curated"
    source = _SOURCES[name]
    if not _PRESETS_PATH.is_file():
        raise _StatFetchError(name, source, f"{_PRESETS_PATH} not found")
    try:
        tree = ast.parse(_PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise _StatFetchError(
            name, source, f"could not parse {_PRESETS_PATH.name}: {exc}"
        ) from exc
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id != _FEATURED_TUPLE_NAME:
                continue
            value = node.value
            if not isinstance(value, ast.Tuple):
                raise _StatFetchError(
                    name,
                    source,
                    f"{_FEATURED_TUPLE_NAME} is not a tuple literal",
                )
            raw = len(value.elts)
            return {"raw": raw, "display": str(raw)}
    raise _StatFetchError(
        name, source, f"{_FEATURED_TUPLE_NAME} not found in {_PRESETS_PATH.name}"
    )


def _fetch_providers_via_litellm() -> StatEntry:
    """Count LLM providers LiteLLM can route to via ``len(litellm.models_by_provider)``.

    ``models_by_provider`` maps each provider to its catalogued models, so
    its length is the number of providers with at least one known model --
    the honest "LLM providers" figure. (``model_cost`` counts individual
    models, not providers, and would massively overstate the provider
    claim.) Rounded down to the nearest ``_LITELLM_PROVIDER_ROUND_TO`` (5)
    and rendered as ``"N+"`` so headline prose stays stable across minor
    LiteLLM bumps.
    """
    name = "providers_via_litellm"
    source = _SOURCES[name]
    try:
        import litellm
    except ImportError as exc:
        raise _StatFetchError(name, source, f"could not import litellm: {exc}") from exc
    by_provider = getattr(litellm, "models_by_provider", None)
    if by_provider is None:
        raise _StatFetchError(
            name, source, "litellm has no models_by_provider attribute"
        )
    try:
        raw = len(by_provider)
    except TypeError as exc:
        raise _StatFetchError(
            name, source, f"models_by_provider is not sized: {exc}"
        ) from exc
    rounded = _round_floor(raw, _LITELLM_PROVIDER_ROUND_TO)
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


def _fetch_convention_gates() -> StatEntry:
    """Count `scripts/check_*.py` enforcement gate scripts (incl. meta-gate)."""
    name = "convention_gates"
    source = _SOURCES[name]
    scripts_dir = REPO_ROOT / "scripts"
    if not scripts_dir.is_dir():
        raise _StatFetchError(name, source, f"scripts/ not found at {scripts_dir}")
    count = sum(1 for _ in scripts_dir.glob("check_*.py"))
    return {"raw": count, "display": str(count)}


_MCP_DOMAINS_DIR: Final[Path] = (
    REPO_ROOT / "src" / "synthorg" / "meta" / "mcp" / "domains"
)
_MCP_TOOL_BUILDERS: Final[frozenset[str]] = frozenset(
    {"read_tool", "write_tool", "admin_tool", "tool_def"}
)


def _count_tool_builders(tree: ast.Module) -> int:
    """Count MCP tool-builder call-sites in a parsed module.

    Walks the AST for ``ast.Call`` nodes whose callee name is one of
    ``_MCP_TOOL_BUILDERS``. An AST scan (rather than a text regex) cannot
    miscount builder names that appear in comments, docstrings, or string
    literals, and it matches builder calls regardless of whitespace or
    line breaks between the name and its argument list.
    """
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        else:
            continue
        if name in _MCP_TOOL_BUILDERS:
            count += 1
    return count


_SETTINGS_ENUMS_PATH: Final[Path] = (
    REPO_ROOT / "src" / "synthorg" / "settings" / "enums.py"
)
_SETTING_NAMESPACE_ENUM: Final[str] = "SettingNamespace"


def _fetch_mcp_tools() -> StatEntry:
    """Count MCP tool definitions via builder call-sites in ``domains/*.py``.

    Counts ``read_tool`` / ``write_tool`` / ``admin_tool`` invocations
    across the domain modules: each call site builds exactly one
    ``MCPToolDef``. ``tool_def`` (the low-level builder the three helpers
    delegate to) is also counted, but the domain modules call it only
    through those helpers. An AST scan avoids importing the service-heavy
    handler graph just to size the surface, and -- unlike a text regex --
    cannot miscount builder names that appear in comments or strings.

    The three failure paths (directory absent, no ``.py`` files, files
    present but zero call-sites) raise distinct ``_StatFetchError``
    reasons; the caller preserves the prior YAML value in every case
    rather than publishing a transient ``0``.
    """
    name = "mcp_tools"
    source = _SOURCES[name]
    if not _MCP_DOMAINS_DIR.is_dir():
        raise _StatFetchError(name, source, f"{_MCP_DOMAINS_DIR} not found")
    py_files = list(_MCP_DOMAINS_DIR.glob("*.py"))
    if not py_files:
        raise _StatFetchError(name, source, "no .py files under domains/")
    count = 0
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _StatFetchError(
                name, source, f"could not read {path.name}: {exc}"
            ) from exc
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            raise _StatFetchError(
                name, source, f"could not parse {path.name}: {exc}"
            ) from exc
        count += _count_tool_builders(tree)
    if count == 0:
        raise _StatFetchError(
            name, source, "domain files present but no tool builder call-sites matched"
        )
    return {"raw": count, "display": str(count)}


def _fetch_mcp_domains() -> StatEntry:
    """Count MCP domain modules (non-underscore ``domains/*.py`` files).

    Distinguishes a missing directory from a present-but-empty one so a
    broken path and a refactor that removes every domain module surface
    as different warnings; the caller preserves the prior value in both.
    """
    name = "mcp_domains"
    source = _SOURCES[name]
    if not _MCP_DOMAINS_DIR.is_dir():
        raise _StatFetchError(name, source, f"{_MCP_DOMAINS_DIR} not found")
    py_files = list(_MCP_DOMAINS_DIR.glob("*.py"))
    if not py_files:
        raise _StatFetchError(name, source, "no .py files under domains/")
    count = sum(1 for p in py_files if not p.name.startswith("_"))
    if count == 0:
        raise _StatFetchError(
            name, source, "domain files present but all are underscore-prefixed"
        )
    return {"raw": count, "display": str(count)}


def _fetch_settings_namespaces() -> StatEntry:
    """Count ``SettingNamespace`` enum members in ``settings/enums.py``.

    The enum is the authoritative namespace registry; an ``ast`` walk
    avoids importing the settings package's initialisation chain.
    """
    name = "settings_namespaces"
    source = _SOURCES[name]
    if not _SETTINGS_ENUMS_PATH.is_file():
        raise _StatFetchError(name, source, f"{_SETTINGS_ENUMS_PATH} not found")
    try:
        tree = ast.parse(_SETTINGS_ENUMS_PATH.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise _StatFetchError(
            name, source, f"could not parse {_SETTINGS_ENUMS_PATH.name}: {exc}"
        ) from exc
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != _SETTING_NAMESPACE_ENUM:
            continue
        members = sum(
            1 for stmt in node.body if isinstance(stmt, ast.Assign | ast.AnnAssign)
        )
        if members == 0:
            raise _StatFetchError(
                name, source, f"{_SETTING_NAMESPACE_ENUM} has no members"
            )
        return {"raw": members, "display": str(members)}
    raise _StatFetchError(
        name,
        source,
        f"{_SETTING_NAMESPACE_ENUM} not found in {_SETTINGS_ENUMS_PATH.name}",
    )


_FETCHERS: dict[str, Callable[[], StatEntry]] = {
    "tests": _fetch_tests,
    "providers_curated": _fetch_providers_curated,
    "providers_via_litellm": _fetch_providers_via_litellm,
    "subagents": _fetch_subagents,
    "convention_gates": _fetch_convention_gates,
    "mcp_tools": _fetch_mcp_tools,
    "mcp_domains": _fetch_mcp_domains,
    "settings_namespaces": _fetch_settings_namespaces,
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


def _load_existing() -> dict[str, object]:
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

    # Prune stats whose fetcher has been removed from _FETCHERS so a
    # decommissioned stat does not linger in the YAML forever. Prior
    # values for retained stats are still preserved on transient fetch
    # failures (the `keeping_prior_value` branch below).
    # Normalise to {} when `stats:` is missing or YAML-null; _load_existing
    # already raises on a non-mapping value.
    existing_stats_raw = existing.get("stats")
    existing_stats = existing_stats_raw if isinstance(existing_stats_raw, dict) else {}
    new_stats: dict[str, object] = {
        name: value for name, value in existing_stats.items() if name in _FETCHERS
    }
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
    output: dict[str, object] = {
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
