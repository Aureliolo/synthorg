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
# Tabs go too, and not only for tidiness: the per-segment verdict loop
# below carries each segment tab-separated from its own approval flag,
# so a tab surviving inside a command would truncate that segment and
# hide the rest of it from the gate.
FLAT=$(printf '%s' "$COMMAND" | tr '\n\r\t' '   ')

# Find the actual pytest INVOCATIONS, not the substring "pytest". A
# `git commit -m "...pytest tests/e2e..."` merely mentions the word and
# must not be flagged. Split the command into segments on `;`, `&&`,
# `||`, `|`; for each, strip leading `VAR=val` env assignments, then
# require that the segment's program is pytest itself, or `python -m
# pytest`, optionally behind a `uv run` / `uvx` / `poetry run` / `time`
# wrapper.
#
# EVERY matching segment is analysed, not the first. Stopping at the
# first one let `pytest tests/unit && pytest tests/e2e` through: the
# allowed segment answered for the whole command and the e2e run behind
# it was never looked at. A chained command runs every segment, so the
# gate's verdict is the strictest of their verdicts.
SEGMENTS=$(printf '%s' "$FLAT" | sed -E 's/&&|\|\||;|\|/\n/g')
PSEGS=""
PSEG_FOUND=0
while IFS= read -r seg; do
    seg="${seg#"${seg%%[![:space:]]*}"}"
    # The approval token must be a leading env assignment on THIS
    # segment, not anywhere in the whole command: a token in an
    # unrelated segment must never unblock a blocked pytest run.
    env_prefix=$(printf '%s' "$seg" \
        | sed -E 's/^(([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*)?.*/\1/')
    seg_allow=0
    if printf '%s' "$env_prefix" \
        | grep -qE '(^|[[:space:]])ALLOW_E2E_TESTS=1([[:space:]]|$)'; then
        seg_allow=1
    fi
    norm=$(printf '%s' "$seg" \
        | sed -E 's/^([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)+//')
    if printf '%s' "$norm" | grep -qE \
        '^(time[[:space:]]+)?((uv[[:space:]]+run|uvx|poetry[[:space:]]+run)[[:space:]]+)?(python[0-9.]*[[:space:]]+-m[[:space:]]+pytest|pytest)([[:space:]]|$)'
    then
        PSEG_FOUND=1
        # Carry each segment with its own approval flag, tab-separated,
        # so the verdict loop below reads the pair that belongs together.
        PSEGS="${PSEGS}${seg_allow}	${norm}
"
    fi
done <<EOF
$SEGMENTS
EOF

# Not a pytest invocation -- no opinion.
if [[ "$PSEG_FOUND" -eq 0 ]]; then
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

# Verdict for ONE pytest segment: 0 to allow, 1 to deny. Returning
# rather than exiting is what lets the caller ask about every segment;
# an `exit 0` in here would allow the whole command on the strength of
# whichever segment happened to be read first.
segment_verdict() {
    local pseg=$1

    # Benchmarks / CodSpeed are single-process by design and not e2e; a
    # separate gate governs them.
    if echo "$pseg" | grep -qE '(--codspeed|tests/benchmarks)'; then
        return 0
    fi

    # Neutralise the `python -m pytest` module flag so it is not mistaken
    # for the pytest `-m` marker selector.
    local scan
    scan=$(printf '%s' "$pseg" \
        | sed -E 's/python[0-9.]*[[:space:]]+-m[[:space:]]+pytest/pytest/g')

    # Extract the -m marker expression (best effort: up to the next flag
    # or quote). Handles `-m unit`, `-m=unit`, `-m "not slow"`. `grep -m1`
    # stops at the first match (no `head`, so no SIGPIPE/pipefail trap);
    # `|| true` keeps a no-marker command from aborting under `set -e`.
    local marker="" mraw
    mraw=$(printf '%s' "$scan" \
        | grep -m1 -oE "(^|[[:space:]])-m[[:space:]=]+['\"]?[a-zA-Z0-9_ ()]+" \
        || true)
    if [[ -n "$mraw" ]]; then
        marker=$(printf '%s' "$mraw" | sed -E "s/.*-m[[:space:]=]+['\"]?//")
    fi

    # A marker that selects e2e/integration is always blocked.
    if echo "$marker" | grep -qiwE 'e2e|integration'; then
        return 1
    fi

    # `-m unit` (and it does not also pull e2e/integration) is the
    # sanctioned gate -- allow even with a broad `tests/` path, because
    # the marker guarantees only unit tests execute.
    if echo "$marker" | grep -qiwE 'unit'; then
        return 0
    fi

    # An explicit e2e / integration path is blocked regardless of marker.
    if echo "$pseg" | grep -qE '(^|[[:space:]=])tests/(e2e|integration)(/|[[:space:]]|$)'; then
        return 1
    fi

    # A single targeted test (node id) is deliberate and bounded -- allow.
    if echo "$pseg" | grep -qE '::'; then
        return 0
    fi

    # A path scoped under tests/unit is the unit suite -- allow.
    if echo "$pseg" | grep -qE '(^|[[:space:]=])tests/unit(/|[[:space:]]|$)'; then
        return 0
    fi

    # Whole-tree / unscoped run: a bare `tests` / `tests/` path, or no
    # `tests` path token at all (bare `pytest` collects from rootdir =
    # the whole tree, including e2e/integration). Block it.
    if echo "$pseg" | grep -qE '(^|[[:space:]=])tests/?([[:space:]]|$)'; then
        return 1
    fi
    if ! echo "$pseg" | grep -qE '(^|[[:space:]=])tests/[^[:space:]]'; then
        return 1
    fi

    # Otherwise the run is scoped to a specific non-e2e/integration path
    # (e.g. tests/conformance/...): deliberate and bounded -- allow.
    return 0
}

# Ask about every pytest segment; the first denied one blocks the whole
# command, because the shell would run it too.
while IFS=$'\t' read -r seg_allow seg_cmd; do
    [[ -z "$seg_cmd" ]] && continue
    # Explicit, per-invocation user approval. This is the ONLY sanctioned
    # way to run the e2e/integration/whole-tree suite. The model must not
    # add this token on its own initiative -- it represents the user
    # having said "yes, run it". Bound to ITS OWN segment, so approving
    # one run never approves a different one chained after it.
    if [[ "$seg_allow" -eq 1 ]]; then
        continue
    fi
    if ! segment_verdict "$seg_cmd"; then
        deny "$DENY_MSG"
    fi
done <<EOF
$PSEGS
EOF

exit 0
