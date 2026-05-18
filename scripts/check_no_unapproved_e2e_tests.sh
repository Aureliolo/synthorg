#!/usr/bin/env bash
# PreToolUse(Bash) hook: never run the e2e / integration / whole-tree
# pytest suite without explicit user approval.
#
# Why this exists:
#   The sanctioned local gate is the UNIT suite only -- the project's
#   CLAUDE.md Quick Commands separate ``pytest tests/ -m unit`` from
#   ``-m integration``, ``-m e2e``, and the CodSpeed benchmarks. A bare
#   ``pytest tests/`` (no marker) collects and RUNS the entire tree:
#   unit + integration + e2e + benchmarks, under walltime, with the
#   pinned 8 workers. That is far slower than the ~3:30 unit baseline,
#   spins up e2e tests that may need Docker / real services, and was
#   never approved. The /pre-pr-review skill historically instructed
#   exactly this. The model must NOT run it on its own initiative.
#
# Sanctioned (allowed) forms:
#   * ``-m unit`` marker (the gate), even with a ``tests/`` path
#   * a path scoped under ``tests/unit`` (with or without a marker)
#   * a single ``path::test_name`` node id (deliberate, bounded)
#   * benchmarks / ``--codspeed`` (single-process by design; a
#     separate gate already governs these)
#   * any command prefixed with ``ALLOW_E2E_TESTS=1`` -- the explicit,
#     per-invocation user-approval escape hatch (mirrors the
#     ``ALLOW_BASELINE_GROWTH=1`` convention)
#
# Blocked forms (require ALLOW_E2E_TESTS=1 after user approval):
#   * ``-m e2e`` / ``-m integration`` (or any marker selecting them)
#   * a ``tests/e2e`` / ``tests/integration`` path
#   * a bare whole-tree run: ``pytest tests/`` or ``pytest`` with no
#     unit scoping at all (collects + runs e2e/integration)
#
# Modes: JSON stdin -> inspect command; no stdin -> pass.
set -euo pipefail

RAW="$(cat)"
# No stdin (pre-commit, not a tool call): not applicable, pass.
if [[ -z "${RAW//[[:space:]]/}" ]]; then
    exit 0
fi
# stdin present: it MUST parse. A malformed envelope is an unknown
# state, not "no opinion" -- fail closed so a corrupted/truncated
# payload cannot silently bypass the gate.
if ! COMMAND=$(printf '%s' "$RAW" | jq -r '.tool_input.command // empty' 2>/dev/null); then
    echo "BLOCKED: malformed PreToolUse JSON envelope; gate fails closed." >&2
    exit 2
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Collapse newlines so a multi-line command is matched as one string.
FLAT=$(printf '%s' "$COMMAND" | tr '\n\r' '  ')

# Find the actual pytest INVOCATION, not the substring "pytest". A
# `git commit -m "...pytest tests/e2e..."` merely mentions the word and
# must not be flagged. Split the command into segments on `;`, `&&`,
# `||`, `|`; for each, strip leading `VAR=val` env assignments, then
# require that the segment's program is pytest itself, or `python -m
# pytest`, optionally behind a `uv run` / `uvx` / `poetry run` / `time`
# wrapper. The first matching segment is the run we analyse.
SEGMENTS=$(printf '%s' "$FLAT" | sed -E 's/&&|\|\||;|\|/\n/g')
PSEG=""
while IFS= read -r seg; do
    seg="${seg#"${seg%%[![:space:]]*}"}"
    norm=$(printf '%s' "$seg" \
        | sed -E 's/^([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)+//')
    if printf '%s' "$norm" | grep -qE \
        '^(time[[:space:]]+)?((uv[[:space:]]+run|uvx|poetry[[:space:]]+run)[[:space:]]+)?(python[0-9.]*[[:space:]]+-m[[:space:]]+pytest|pytest)([[:space:]]|$)'
    then
        PSEG="$norm"
        break
    fi
done <<EOF
$SEGMENTS
EOF

# Not a pytest invocation -- no opinion.
if [[ -z "$PSEG" ]]; then
    exit 0
fi

# Explicit, per-invocation user approval. This is the ONLY sanctioned
# way to run the e2e/integration/whole-tree suite. The model must not
# add this token on its own initiative -- it represents the user
# having said "yes, run it".
if echo "$FLAT" | grep -qE '(^|[[:space:]])ALLOW_E2E_TESTS=1([[:space:]]|$)'; then
    exit 0
fi

# Benchmarks / CodSpeed are single-process by design and not e2e; a
# separate gate governs them.
if echo "$PSEG" | grep -qE '(--codspeed|tests/benchmarks)'; then
    exit 0
fi

deny() {
    local reason=$1
    local escaped
    escaped=$(printf '%s' "$reason" | jq -Rsa .)
    cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": $escaped
  }
}
ENDJSON
    exit 2
}

DENY_MSG=$(cat <<'MSG'
BLOCKED: this pytest run would execute the e2e / integration / whole
test tree, which must NEVER run without explicit user approval. A bare
`pytest tests/` (no marker) collects AND runs unit + integration + e2e
+ benchmarks -- far slower than the unit baseline and not approved.

Use one of the sanctioned forms instead:
  * `uv run python -m pytest tests/ -m unit`   (the unit gate)
  * a path under `tests/unit/...`
  * a single `path::test_name` node id (one targeted test)

To run e2e / integration / the full suite, ASK THE USER FIRST, then
prefix the approved command with `ALLOW_E2E_TESTS=1` for that single
invocation. Do not add that token on your own initiative.
MSG
)

# Neutralise the `python -m pytest` module flag so it is not mistaken
# for the pytest `-m` marker selector.
SCAN=$(printf '%s' "$PSEG" \
    | sed -E 's/python[0-9.]*[[:space:]]+-m[[:space:]]+pytest/pytest/g')

# Extract the -m marker expression (best effort: up to the next flag
# or quote). Handles `-m unit`, `-m=unit`, `-m "not slow"`. `grep -m1`
# stops at the first match (no `head`, so no SIGPIPE/pipefail trap);
# `|| true` keeps a no-marker command from aborting under `set -e`.
MARKER=""
MRAW=$(printf '%s' "$SCAN" \
    | grep -m1 -oE "(^|[[:space:]])-m[[:space:]=]+['\"]?[a-zA-Z0-9_ ()]+" \
    || true)
if [[ -n "$MRAW" ]]; then
    MARKER=$(printf '%s' "$MRAW" | sed -E "s/.*-m[[:space:]=]+['\"]?//")
fi

# A marker that selects e2e/integration is always blocked.
if echo "$MARKER" | grep -qiwE 'e2e|integration'; then
    deny "$DENY_MSG"
fi

# `-m unit` (and it does not also pull e2e/integration) is the
# sanctioned gate -- allow even with a broad `tests/` path, because
# the marker guarantees only unit tests execute.
if echo "$MARKER" | grep -qiwE 'unit'; then
    exit 0
fi

# An explicit e2e / integration path is blocked regardless of marker.
if echo "$PSEG" | grep -qE '(^|[[:space:]=])tests/(e2e|integration)(/|[[:space:]]|$)'; then
    deny "$DENY_MSG"
fi

# A single targeted test (node id) is deliberate and bounded -- allow.
if echo "$PSEG" | grep -qE '::'; then
    exit 0
fi

# A path scoped under tests/unit is the unit suite -- allow.
if echo "$PSEG" | grep -qE '(^|[[:space:]=])tests/unit(/|[[:space:]]|$)'; then
    exit 0
fi

# Whole-tree / unscoped run: a bare `tests` / `tests/` path, or no
# `tests` path token at all (bare `pytest` collects from rootdir =
# the whole tree, including e2e/integration). Block it.
if echo "$PSEG" | grep -qE '(^|[[:space:]=])tests/?([[:space:]]|$)'; then
    deny "$DENY_MSG"
fi
if ! echo "$PSEG" | grep -qE '(^|[[:space:]=])tests/[^[:space:]]'; then
    deny "$DENY_MSG"
fi

# Otherwise the run is scoped to a specific non-e2e/integration path
# (e.g. tests/conformance/...): deliberate and bounded -- allow.
exit 0
