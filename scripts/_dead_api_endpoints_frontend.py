"""Frontend URL inventory: scan ``web/src/**/*.{ts,tsx}`` for call sites.

We do NOT pull in a TypeScript AST -- the codebase patterns are
regular enough that a hand-rolled token scanner is faster, has zero
Node dependency, and produces deterministic output across platforms.

Patterns recognised
-------------------

1. ``apiClient.<method>(<URL_EXPR>, ...)`` -- the dominant call shape.
2. ``fetch(<URL_EXPR>, <init>)`` where ``<init>`` carries
   ``method: '<METHOD>'``. Bare ``fetch(url)`` is treated as GET.

URL expression shapes
---------------------

- Bare string literal: ``'/agents'``.
- Template literal with ``${expr}``: e.g. ``/agents/${encodeURIComponent(name)}``
  -- ``${expr}`` is normalised to ``{*}`` (wildcard placeholder).
- Template literal beginning with ``${BASE}`` where ``BASE`` is a
  module-level ``const BASE = '/path'`` -- BASE is resolved by
  reading every same-file ``const <NAME> = '<literal>'`` declaration.
- Template literal beginning with ``${apiClient.defaults.baseURL}``,
  ``${baseUrl}``, or ``${base}.../api/v1/...`` -- the base prefix is
  recognised as "Axios base"; the substituted form is then stripped
  back to the path (the ``/api/v1`` prefix is dropped to match the
  backend coordinate system).

Suppression
-----------

Any call-site line carrying ``// lint-allow: dead-api-endpoints --
<reason>`` is recorded with ``has_suppression=True`` so the
comparator skips it.

Limitations
-----------

Call sites whose URL is built from non-recognised variables, function
calls, or other dynamic expressions (e.g. ``apiClient.get(buildPath(x))``,
``apiClient.get(`${arbitraryVar}/x`)``) are dropped from the inventory
because the gate cannot statically resolve them. Such call sites are
invisible to the parity check; if a backend route they target gets
removed, the runtime will 404 without the gate flagging it. The
recommended workaround is to inline the URL or use one of the
recognised "base"-style heads (``${BASE}`` const, ``${baseUrl}``).
"""

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dead_api_endpoints_models import (  # type: ignore[import-not-found]
        CallSiteRecord,
        _line_has_js_marker,
        normalise_path,
    )
else:
    from scripts._dead_api_endpoints_models import (
        CallSiteRecord,
        _line_has_js_marker,
        normalise_path,
    )

# ── Constants ──────────────────────────────────────────────────

# apiClient.METHOD ... ( -- captures the method name. After the
# match we manually skip any TS generic parameters
# (``<PaginatedResponse<AgentConfig>>``) before locating the "(".
_APICLIENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\bapiClient\s*\.\s*(?P<method>get|post|put|patch|delete)\b"
)
"""Match ``apiClient.METHOD`` regardless of subsequent generic
parameters. The arity-aware bracket walker in
:func:`_skip_generics_and_open_paren` handles the
``<PaginatedResponse<AgentConfig>>`` case where the regex alone would
trip over nested ``<>``."""

_FETCH_RE: Final[re.Pattern[str]] = re.compile(r"\bfetch\s*\(")
"""Match raw ``fetch(`` calls."""

_METHOD_KW_RE: Final[re.Pattern[str]] = re.compile(
    r"""method\s*:\s*['"](?P<m>get|post|put|patch|delete|head|options)['"]""",
    re.IGNORECASE,
)
"""Pull a literal ``method:`` value out of a ``fetch(...)`` init object."""

_CONST_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*const\s+(?P<name>[A-Z_][A-Z0-9_]*)\s*(?::\s*string)?\s*=\s*['\"](?P<value>/[^'\"]*)['\"]",
    re.MULTILINE,
)
"""Same-file ``const NAME = '/path'`` declarations. Restricted to
SCREAMING_SNAKE names so the recogniser stays specific (``BASE``,
``API_BASE``, ``MEMORY_BASE``); lower-case ``baseUrl`` is intentionally
NOT a const-resolution candidate -- it's the ``apiClient.defaults.baseURL``
reference handled as ``API_BASE_DYNAMIC`` below."""

_TS_GLOB: Final[tuple[str, ...]] = ("*.ts", "*.tsx")

_API_PREFIX: Final[str] = "/api/v1"

_MIN_QUOTED_LEN: Final[int] = 2
"""Minimum length of a delimited string literal (opening + closing quote)."""

_BASE_DYNAMIC_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "baseUrl",
        "base",
        "apiClient.defaults.baseURL",
        "import.meta.env.VITE_API_BASE_URL",
    }
)
"""Identifier / member-chain tokens treated as 'the Axios base URL'.
When a template literal starts with one of these, the substitute
yields a path that begins with ``/api/v1`` (or just ``/...``); we
detect both and drop the API prefix so the path lines up with the
backend coordinate system."""


# ── Helpers ────────────────────────────────────────────────────


def _skip_generics_and_open_paren(text: str, start: int) -> int | None:
    """Skip whitespace + an optional ``<...>`` generic block + return the ``(`` offset.

    Walks character-by-character so nested generics (``<A<B>>``) are
    matched correctly. Returns the index of the opening ``(`` or
    ``None`` if no call follows the matched ``apiClient.METHOD``
    expression.
    """
    i = start
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return None
    if text[i] == "<":
        depth = 1
        i += 1
        while i < n and depth > 0:
            ch = text[i]
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            i += 1
        if depth != 0:
            return None
    while i < n and text[i].isspace():
        i += 1
    if i < n and text[i] == "(":
        return i
    return None


def _line_col_at(text: str, offset: int) -> tuple[int, int]:
    """Return the (1-based-line, 0-based-col) of *offset* in *text*."""
    line = text.count("\n", 0, offset) + 1
    last_nl = text.rfind("\n", 0, offset)
    col = offset - (last_nl + 1)
    return line, col


# State machine for nested parens / strings / templates. The
# bracket-depth tracker has to be threaded through string-literal
# context ('', "", ``), template-substitution depth (``${...}``), and
# backslash escape sequences in a single forward pass. Splitting any
# of these branches into helpers would fragment the per-character
# flow and make the parser harder to reason about, so the function
# keeps its C901 / PLR0912 suppressions rather than being broken up.
def _iter_top_level_positions(
    text: str,
    paren_idx: int,
) -> Iterator[tuple[str, int]]:
    r"""Walk *text* from ``paren_idx + 1`` and yield top-level events.

    ``paren_idx`` points at the opening ``(``. We track matching
    parens / brackets / braces, single / double / backtick string
    literals, ``${...}`` template substitutions, and ``\`` escapes,
    then yield events at each position the call site cares about:

    - ``("comma", i)`` when ``,`` appears at ``depth == 1``.
    - ``("close", i)`` when the closing ``)`` / ``]`` / ``}`` brings
      the depth back to zero. The generator stops after yielding
      ``"close"``.

    Yields nothing if EOS is reached before the parens balance.
    """
    depth = 1
    i = paren_idx + 1
    in_single = in_double = in_backtick = False
    template_depth = 0
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and (in_single or in_double or in_backtick):
            escape = True
            i += 1
            continue
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == '"':
                in_double = False
        elif in_backtick:
            if ch == "`" and template_depth == 0:
                in_backtick = False
            elif ch == "$" and i + 1 < len(text) and text[i + 1] == "{":
                template_depth += 1
                i += 2
                continue
            elif ch == "}" and template_depth > 0:
                template_depth -= 1
        elif ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            in_backtick = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                yield ("close", i)
                return
        elif ch == "," and depth == 1:
            yield ("comma", i)
        i += 1


def _extract_url_expression(
    text: str,
    start: int,
) -> tuple[str, int] | None:
    """Read the first argument of a function call starting at *start*.

    *start* points at the opening ``(``. Returns
    ``(expression_text, end_offset)`` for the first top-level argument
    (the slice up to the first top-level ``,`` or matching ``)``), or
    ``None`` if the parens close before any non-whitespace argument
    appears.
    """
    if start >= len(text) or text[start] != "(":
        return None
    arg_start = start + 1
    for _kind, end in _iter_top_level_positions(text, start):
        return text[arg_start:end].strip(), end
    return None


def _resolve_template_literal(
    expr: str,
    constants: dict[str, str],
) -> str | None:
    """Resolve a template-literal URL expression to a path string.

    The expression is either:

    - A string literal: ``'/agents'`` or ``"/agents"``.
    - A template literal (backtick-delimited) such as
      ``/agents/${var}`` with arbitrary ``${...}`` substitutions.

    Returns the resolved path with every ``${expr}`` replaced by
    ``{*}``. Recognised "base"-style heads (``${BASE}`` const,
    ``${baseUrl}`` Axios base, etc.) are substituted in:

    - SCREAMING_SNAKE constants get their literal value.
    - Axios-base tokens get ``""`` (the base resolves to ``/api/v1``
      at runtime; we strip that prefix anyway, so substituting ``""``
      yields the same comparator coordinate).

    Returns ``None`` if the expression is not a static URL (a function
    call, a variable that is not a recognised base, etc.).
    """
    expr = expr.strip()
    if not expr:
        return None
    # Plain string literal. ``_MIN_QUOTED_LEN = 2`` covers the two
    # quote characters; anything shorter cannot be a delimited string.
    if (
        expr.startswith(("'", '"'))
        and expr[0] == expr[-1]
        and len(expr) >= _MIN_QUOTED_LEN
    ):
        return expr[1:-1]
    if not (expr.startswith("`") and expr.endswith("`")):
        return None
    body = expr[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "$" and i + 1 < len(body) and body[i + 1] == "{":
            close = _find_template_end(body, i + 2)
            if close == -1:
                return None
            inner = body[i + 2 : close].strip()
            substitution = _substitute_template_expr(inner, constants)
            out.append(substitution)
            i = close + 1
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def _find_template_end(body: str, start: int) -> int:
    """Find the matching ``}`` for a ``${...}`` opening at *start*.

    Tracks nested braces and bracketed expressions inside the
    substitution. Returns the index of the closing ``}`` or -1 if
    unbalanced.
    """
    depth = 1
    i = start
    in_single = in_double = in_backtick = False
    while i < len(body):
        ch = body[i]
        if in_single:
            if ch == "'":
                in_single = False
            elif ch == "\\":
                i += 2
                continue
        elif in_double:
            if ch == '"':
                in_double = False
            elif ch == "\\":
                i += 2
                continue
        elif in_backtick:
            if ch == "`":
                in_backtick = False
            elif ch == "\\":
                i += 2
                continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        elif ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            in_backtick = True
        i += 1
    return -1


def _substitute_template_expr(inner: str, constants: dict[str, str]) -> str:
    """Substitute one ``${inner}`` expression to its path-fragment value.

    Recognised forms (in priority order):

    - SCREAMING_SNAKE bare identifier matching a same-file const ->
      const literal value.
    - Recognised Axios-base token (``baseUrl``,
      ``apiClient.defaults.baseURL``, ``import.meta.env.VITE_API_BASE_URL``,
      or any of those followed by a method-chain like ``.replace(...)``)
      -> empty string, because the base resolves to ``/api/v1`` and we
      strip that prefix. Multi-segment tokens are matched as a whole
      prefix (``apiClient.defaults.baseURL.replace(...)`` matches the
      ``apiClient.defaults.baseURL`` token), not just the first dotted
      segment, so they don't fall through to ``{*}``.
    - ``encodeURIComponent(<name>)`` -> ``{<name>}`` so the path-param
      name surfaces in the comparator output (cosmetic only;
      :func:`normalise_path` collapses to ``{*}`` before compare).
    - Anything else -> ``{*}``.
    """
    bare = inner.strip()
    if bare in constants:
        return constants[bare]
    if any(bare == t or bare.startswith(t + ".") for t in _BASE_DYNAMIC_TOKENS):
        return ""
    enc = re.match(r"encodeURIComponent\s*\(\s*(?P<name>[A-Za-z_$][\w$]*)", bare)
    if enc is not None:
        return "{" + enc.group("name") + "}"
    return "{*}"


def _path_after_api_prefix(path: str) -> str:
    """Strip ``/api/v1`` from the head of *path*.

    Aligns with the backend's prefix-stripped coordinate system.
    Idempotent.
    """
    if path.startswith(_API_PREFIX + "/"):
        return path[len(_API_PREFIX) :]
    if path == _API_PREFIX:
        return "/"
    return path


def _scan_file(
    rel: str,
    text: str,
) -> list[CallSiteRecord]:
    """Return every call-site record found in *text*.

    *rel* is the POSIX-style path of the source file relative to the
    repository root, used as the ``source_file`` field.
    """
    constants = {m.group("name"): m.group("value") for m in _CONST_RE.finditer(text)}
    records: list[CallSiteRecord] = []
    lines = text.splitlines()
    seen_offsets: set[int] = set()

    for match in _APICLIENT_RE.finditer(text):
        method = (match.group("method") or "").upper()
        if not method:
            continue
        paren_idx = _skip_generics_and_open_paren(text, match.end())
        if paren_idx is None or paren_idx in seen_offsets:
            continue
        seen_offsets.add(paren_idx)
        record = _emit_record(text, lines, paren_idx, method, rel, constants)
        if record is not None:
            records.append(record)

    for match in _FETCH_RE.finditer(text):
        # Skip ``apiClient.fetch`` (would be matched by APICLIENT_RE)
        # and any spurious ``something.fetch``: only bare ``fetch(`` counts.
        prev_idx = match.start() - 1
        if prev_idx >= 0 and (text[prev_idx].isalnum() or text[prev_idx] in "._"):
            continue
        paren_idx = match.end() - 1
        if paren_idx in seen_offsets:
            continue
        seen_offsets.add(paren_idx)
        method = _extract_fetch_method(text, paren_idx) or "GET"
        record = _emit_record(text, lines, paren_idx, method, rel, constants)
        if record is not None:
            records.append(record)

    return records


def _emit_record(  # noqa: PLR0913 -- helper takes pre-computed scan state
    text: str,
    lines: list[str],
    paren_idx: int,
    method: str,
    rel: str,
    constants: dict[str, str],
) -> CallSiteRecord | None:
    """Read the URL expression at *paren_idx* and emit a record."""
    extracted = _extract_url_expression(text, paren_idx)
    if extracted is None:
        return None
    expr, _ = extracted
    resolved = _resolve_template_literal(expr, constants)
    if resolved is None:
        return None
    if not resolved.startswith("/"):
        # Relative URLs and non-API endpoints (e.g. ``http://...``)
        # are out of scope; only paths that look like API routes
        # (start with ``/``) are tracked.
        return None
    path = normalise_path(_path_after_api_prefix(resolved))
    line, col = _line_col_at(text, paren_idx)
    has_suppression = False
    if 1 <= line <= len(lines):
        has_suppression = _line_has_js_marker(lines[line - 1])
    return CallSiteRecord(
        method=method,
        path=path,
        source_file=rel,
        source_line=line,
        source_col=col,
        has_suppression=has_suppression,
    )


def _extract_fetch_method(text: str, paren_idx: int) -> str | None:
    """Look ahead from a ``fetch(`` call for ``method: 'POST'``.

    Reads up to the first matching ``)`` and searches the slice for
    a ``method:`` literal; returns the uppercased method or ``None``
    if no method is declared (caller defaults to GET).
    """
    for kind, end in _iter_top_level_positions(text, paren_idx):
        if kind == "close":
            body = text[paren_idx + 1 : end]
            m = _METHOD_KW_RE.search(body)
            return m.group("m").upper() if m else None
    return None


def _iter_ts_files(web_root: Path) -> list[Path]:
    """Return every .ts / .tsx file under *web_root*.

    We deliberately do NOT scan ``__tests__`` -- test files routinely
    contain mock URLs that look like dead endpoints (``apiClient.get('/foo')``
    where the test stubs the response). Type-definition files
    (``.d.ts``) are also excluded.
    """
    files: list[Path] = []
    if not web_root.is_dir():
        return files
    for pat in _TS_GLOB:
        for p in web_root.rglob(pat):
            rel = p.relative_to(web_root).as_posix()
            if "__tests__" in rel or rel.endswith(".d.ts"):
                continue
            files.append(p)
    return sorted(files)


def collect_frontend_call_sites(
    project_root: Path,
) -> list[CallSiteRecord]:
    """Scan ``web/src/`` and return every API call-site record.

    File-system errors (unreadable files, encoding errors) skip the
    file but emit a one-line warning to stderr so a corrupt or
    permission-denied .ts file is visible to the operator -- otherwise
    the gate would silently miss every call site in that file and
    inflate orphan-backend findings.
    """
    web_src = project_root / "web" / "src"
    out: list[CallSiteRecord] = []
    for path in _iter_ts_files(web_src):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"check_dead_api_endpoints: cannot read {path}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        rel = path.relative_to(project_root).as_posix()
        out.extend(_scan_file(rel, text))
    return out
