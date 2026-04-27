---
name: block-double-push
enabled: true
event: bash
pattern: \bgit\s+push\b
action: warn
---

**Push throttle (PR-only).**

If an open PR exists for the current branch, this rule fires. Per the project's "ONE push per review round" rule (see `feedback_push_and_review_discipline.md` §4), each push triggers full CI + CodeRabbit re-runs and burns real money / quota. Batch all pending fixes (CI failures + reviewer findings + your own follow-ups) into a single commit chain, then push once.

If a second push within the throttle window (default 5 minutes) is genuinely required, the override is one-shot and out-of-band. **The model cannot create the override itself**; the script `scripts/check_no_throttle_override_creation.sh` rejects any `Bash`, `Write`, or `Edit` tool call referencing the flag path. The user creates the flag in their own shell:

```bash
printf '%s\n' "$(git branch --show-current)" >.claude/state/allow-double-push.flag
git push <args>
```

The flag is consumed (deleted) on use; each override authorises exactly one push.

Backstop: `scripts/check_push_throttle.sh` runs as a PreToolUse-Bash hook and enforces the 5-minute minimum interval between pushes to the same branch when a PR exists. Outside a PR, normal feature pushes are unthrottled.
