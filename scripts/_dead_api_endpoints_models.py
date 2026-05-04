"""Frozen dataclasses + suppression-marker helpers for the dead-API gate.

Extracted from :mod:`scripts.check_dead_api_endpoints` to keep that
module under the 800-line per-file ceiling. Behaviour is unchanged;
the entry script re-exports the names below so tests and callers see
one logical module.
"""

import io
import tokenize
from dataclasses import dataclass
from typing import Final, Literal

# ── Suppression markers ────────────────────────────────────────

_SUPPRESSION_MARKER: Final[str] = "lint-allow: dead-api-endpoints"
"""Shared marker text. Both the Python tokenizer (``# lint-allow: ...``)
and the JS/TS scanner (``// lint-allow: ...``) look for this string."""

_BASELINE_FIELDS = 5
"""Number of colon-separated fields in a baseline entry: ``file:line:col:method:url``."""

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing dead-API-endpoint findings
# (frontend call sites with no matching backend route).
# Each line is `<file>:<line>:<col>:<method>:<url>` sorted in
# deterministic order.
#
# scripts/check_dead_api_endpoints.py reads this file to suppress
# violations at these exact entries. New violations NOT in this list
# will fail the pre-push hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_dead_api_endpoints.py --update-baseline
"""

# ── Domain models ──────────────────────────────────────────────

Severity = Literal["high", "info"]
"""``high`` = frontend → backend mismatch (gate fails). ``info`` =
backend → frontend orphan (printed but does not block; could be a
public REST / CLI consumer)."""


@dataclass(frozen=True)
class RouteRecord:
    """A single backend HTTP / WebSocket route registered with Litestar.

    Invariants:

    - ``method`` is uppercased (``GET``, ``POST``, ..., ``WS``).
    - ``path`` is normalised so every Litestar typed path-param
      ``{name:str}`` is collapsed to ``{name}`` and the API prefix
      (e.g. ``/api/v1``) is **already stripped** -- frontend URLs
      omit it (Axios's ``baseURL`` handles it). The single exception
      is the A2A well-known agent card which is mounted at the app
      root (``/.well-known/...``); for that family the prefix is
      never present in the first place, so no strip happens.
    - ``controller_name`` is the bare class name (``AgentController``),
      used when reporting orphan endpoints.
    """

    method: str
    path: str
    controller_name: str
    source_file: str
    source_line: int


@dataclass(frozen=True)
class CallSiteRecord:
    """A single frontend call site that hits the backend.

    Invariants:

    - ``method`` is uppercased.
    - ``path`` has every ``${expr}`` substituted to ``{<dyn>}`` or to
      ``{<name>}`` when the expression is a recognised
      ``encodeURIComponent(<name>)`` form.
    - ``has_suppression`` is True when the source line carries
      ``// lint-allow: dead-api-endpoints -- <reason>``.
    """

    method: str
    path: str
    source_file: str
    source_line: int
    source_col: int
    has_suppression: bool

    def baseline_key(self) -> str:
        """Compact key used in the baseline file format."""
        return f"{self.source_file}:{self.source_line}:{self.source_col}:{self.method}:{self.path}"


@dataclass(frozen=True)
class Violation:
    """A single dead-endpoint or orphan-endpoint finding."""

    severity: Severity
    method: str
    path: str
    source_file: str
    source_line: int
    source_col: int
    reason: str

    def baseline_key(self) -> str:
        """Baseline key matches :meth:`CallSiteRecord.baseline_key` for HIGH findings."""
        return f"{self.source_file}:{self.source_line}:{self.source_col}:{self.method}:{self.path}"


# ── Path-param normalisation ───────────────────────────────────

# Litestar typed path-params: ``{name:str}`` / ``{version:int}`` /
# ``{id:uuid}`` / ``{x:float}``. The colon-and-suffix is dropped so
# the backend path lines up with the frontend's ``${var}`` form (which
# carries no type).
_TYPED_PARAM_TYPES: Final[tuple[str, ...]] = (
    "str",
    "int",
    "uuid",
    "float",
    "path",
    "decimal",
    "date",
    "time",
    "datetime",
    "timedelta",
)


def normalise_param_name(raw: str) -> str:
    """Strip Litestar type suffix from one path-param token.

    ``"agent_name:str"`` -> ``"agent_name"``; ``"version:int"`` ->
    ``"version"``; ``"plain"`` -> ``"plain"`` (unchanged).
    """
    if ":" not in raw:
        return raw
    name, _, suffix = raw.partition(":")
    if suffix in _TYPED_PARAM_TYPES:
        return name
    return raw


def normalise_path(path: str) -> str:
    """Apply path-param-name normalisation across every ``{...}`` token.

    Joins are pure string ops -- no path-segment splitting -- so
    bare leading slashes / trailing slashes are preserved verbatim.
    Frontend and backend paths flow through the same normaliser so
    ``/agents/{agent_name:str}`` (backend) and ``/agents/{agent_name}``
    (frontend) compare equal.
    """
    out: list[str] = []
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "{":
            close = path.find("}", i)
            if close == -1:  # unterminated; bail and keep verbatim
                out.append(path[i:])
                break
            inner = path[i + 1 : close]
            out.append("{" + normalise_param_name(inner) + "}")
            i = close + 1
        else:
            out.append(ch)
            i += 1
    result = "".join(out)
    # Collapse the placeholder identity: every dynamic frontend
    # segment compares equal to every backend path-param. This makes
    # the comparator's set-membership match positional rather than
    # name-sensitive.
    result_collapsed: list[str] = []
    j = 0
    while j < len(result):
        ch = result[j]
        if ch == "{":
            close = result.find("}", j)
            if close == -1:
                result_collapsed.append(result[j:])
                break
            result_collapsed.append("{*}")
            j = close + 1
        else:
            result_collapsed.append(ch)
            j += 1
    # Collapse a trailing slash (``"/agents/"`` -> ``"/agents"``) so a
    # backend ``@post("/")`` whose composed form is ``/agents/`` lines
    # up with a frontend ``apiClient.post('/agents')``. The bare root
    # ``"/"`` is preserved.
    final = "".join(result_collapsed)
    if len(final) > 1 and final.endswith("/"):
        final = final[:-1]
    return final


# ── Suppression-marker tokenisers ──────────────────────────────


def _line_has_python_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    The marker name (``lint-allow: dead-api-endpoints``) must be
    followed by `` -- `` (a separator with surrounding whitespace) and
    non-empty justification text. Mirrors the
    :mod:`check_persistence_boundary` and :mod:`bootstrap-wiring`
    gates so the suppression shape stays uniform across gates.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


def _line_has_js_marker(line: str) -> bool:  # noqa: C901, PLR0912 -- string-literal-aware line scanner
    """Return True iff a JS/TS *line* carries a trailing ``//`` marker.

    Naive single-line `//` scan: locates the rightmost `//` that is
    NOT inside a string literal, then checks the comment body for
    ``lint-allow: dead-api-endpoints -- <reason>``. Block comments
    (``/* ... */``) are not supported because the suppression
    convention is one-line per call site; trailing block-comment
    markers on the same line as a call are not idiomatic in this
    codebase.

    String-literal detection is intentionally simplistic (single /
    double / backtick quotes with backslash-escape handling). The
    scanner runs after the Litestar AST walk, so any genuinely
    pathological JS construct that breaks this -- regex literals,
    nested template literals containing ``//`` -- can still get an
    explicit per-line opt-out at a different position.
    """
    in_single = False
    in_double = False
    in_backtick = False
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if not (in_single or in_double or in_backtick):
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "`":
                in_backtick = True
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                comment = line[i + 2 :].strip()
                if not comment.startswith(_SUPPRESSION_MARKER):
                    return False
                suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
                if suffix.startswith("--"):
                    justification = suffix[2:].strip()
                    if bool(justification):
                        return True
                return False
        elif in_single and ch == "'":
            in_single = False
        elif in_double and ch == '"':
            in_double = False
        elif in_backtick and ch == "`":
            in_backtick = False
        i += 1
    return False
