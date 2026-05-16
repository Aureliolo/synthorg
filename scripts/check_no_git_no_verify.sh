#!/usr/bin/env bash
# check_no_git_no_verify.sh
# PreToolUse hook: hard-blocks git hook/signing bypass flags.
#
# Blocks any git invocation that disables pre-commit/pre-push hooks or
# commit signing:
#   --no-verify, -n (on commit/push), --no-gpg-sign,
#   -c commit.gpgsign=false, -c core.hooksPath=...
#
# These bypass the project's quality gates. They are only permitted
# with explicit, per-invocation user approval (the agent must ask via
# AskUserQuestion and the user must say yes BEFORE the command runs).
#
# Two modes:
#   1. JSON stdin (Claude Code / OpenCode): inspect the command, block.
#   2. No stdin (pre-commit stage): not applicable -> pass.

set -euo pipefail

# Capture stdin once so we can distinguish "no payload" (a non-tool
# invocation, e.g. the pre-commit stage) from "payload present but
# unparseable". Silently exit-0 on a parse failure would let a
# malformed hook payload disable this guard entirely.
INPUT="$(cat)"
if [[ -z "${INPUT//[$' \t\r\n']/}" ]]; then
    # Empty stdin: not a Claude Code / OpenCode tool call -> nothing
    # to police (fast path).
    exit 0
fi

if ! COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty'); then
    # Non-empty stdin that does not parse as the expected hook JSON:
    # fail closed (deny) rather than silently allowing the command.
    echo "check_no_git_no_verify: unparseable hook payload; denying." >&2
    exit 2
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Only police git commands.
if ! echo "$COMMAND" | grep -qE '(^|[^[:alnum:]_])git([[:space:]]|$)'; then
    exit 0
fi

# Bypass-flag patterns (word-boundary anchored so we don't trip on
# substrings inside paths or commit messages).
BYPASS_RE='(--no-verify([[:space:]=]|$)|--no-gpg-sign([[:space:]=]|$)|-c[[:space:]]+commit\.gpgsign=false|-c[[:space:]]+core\.hooksPath=)'

# Short-form ``-n`` is ``--no-verify`` on ``git commit`` (and the
# documented bypass surface for ``git push``); the long-form regex
# above does not see it. Match ``-n`` only as a standalone token after
# a ``commit``/``push`` subcommand so we don't trip on ``-n`` embedded
# in a path or commit message.
SHORT_NO_VERIFY_RE='git[[:space:]]([^|;&]*[[:space:]])?(commit|push)([[:space:]][^|;&]*)?[[:space:]]-n([[:space:]]|$)'

# Case-insensitive: git config keys are case-insensitive, so
# ``-c core.hookspath=`` / ``-c commit.gpgSign=false`` are accepted by
# git and must be caught the same as their canonical-case spellings.
if echo "$COMMAND" | grep -qiE "$BYPASS_RE" \
    || echo "$COMMAND" | grep -qiE "$SHORT_NO_VERIFY_RE"; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: git hook/signing bypass flag detected (--no-verify / --no-gpg-sign / -c commit.gpgsign=false / -c core.hooksPath=). These skip the project's pre-commit/pre-push gates and are forbidden by default. If a hook is genuinely broken, FIX THE HOOK (e.g. re-run `uv run pre-commit install` so the shared core.hooksPath wrapper points at a live venv) rather than bypassing it. If a bypass is truly unavoidable for this single push, you MUST first ask the user with AskUserQuestion and get an explicit yes; only then re-issue the command."
  }
}
ENDJSON
    exit 2
fi

exit 0
