#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): block em-dashes BEFORE the write lands.
#
# The pre-commit gate scripts/check_no_em_dashes.py catches em-dashes after
# the fact, which means a writer-then-reviewer round-trip per offence. This
# hook fires earlier, on the Edit/Write tool call itself, so the violation
# never reaches disk.
#
# Patterns blocked:
#   - U+2014 EM DASH (the literal character)
#   - The HTML named, decimal, and hex entities for U+2014.
# (Patterns are reconstructed at runtime via shell concatenation so this
# script's source stays clear of every literal blocked pattern -- mirrors
# the same trick in scripts/check_no_em_dashes.py.)
#
# Excluded paths (mirrored from check_no_em_dashes.py):
#   .github/CHANGELOG.md  -- regenerated from history by release-please.
#
# Exit behaviour:
#   - No em-dash in the candidate content: exit 0 (allow)
#   - Em-dash detected: print JSON deny envelope, exit 2

set -euo pipefail

INPUT=$(cat || true)
if [[ -z "$INPUT" ]]; then
    exit 0
fi

FILE_PATH=$(jq -r '.tool_input.file_path // ""' <<<"$INPUT" 2>/dev/null || true)
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Skip regenerated changelog (release-please rewrites this file from git history).
case "$FILE_PATH" in
    */.github/CHANGELOG.md|.github/CHANGELOG.md)
        exit 0
        ;;
esac

# Extract content: Write tool uses .tool_input.content, Edit tool uses .tool_input.new_string.
CONTENT=$(jq -r '.tool_input.content // .tool_input.new_string // ""' <<<"$INPUT" 2>/dev/null || true)
if [[ -z "$CONTENT" ]]; then
    exit 0
fi

EM_DASH=$'\xe2\x80\x94'
AMP='&'
HTML_NAMED="${AMP}mdash;"
HTML_DEC="${AMP}#8212;"
HTML_HEX="${AMP}#x2014;"

if grep -qF -e "$EM_DASH" -e "$HTML_NAMED" -e "$HTML_DEC" -e "$HTML_HEX" <<<"$CONTENT"; then
    REASON="Em-dash (U+2014) detected in the content being written. Replace with the ASCII punctuation that matches the sentence: ':' (colon), ';' (semicolon), ',' (comma), '.' (period), '( ... )' (parens), or '-' (hyphen, compound only). Bare '--' is almost never right; prefer one of the above or rewrite."
    jq -nc \
        --arg reason "$REASON" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
    exit 2
fi

exit 0
