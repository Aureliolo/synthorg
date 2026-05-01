---
description: "Watch a PR after creation. Polls CI + external reviewer state + open code-scanning/Dependabot/secret-scanning alerts, auto-fixes valid feedback (one push per round), dismisses justified security alerts via API with reason, handles CodeRabbit rate-limit by reposting `@coderabbitai review`, runs until convergence or merged. No local-agent invocation, no approval gate."
argument-hint: "[PR# or blank] [cadence default 15m] [max-rounds default 24]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - AskUserQuestion
  - ScheduleWakeup
---

# /babysit-pr

Self-contained watchdog for the post-PR-creation phase. Sits between you and a PR until either (a) it's merged/closed, (b) CI green AND no actionable external feedback remains, or (c) you stop it.

**Does NOT** invoke `/aurelio-review-pr` or any local review agent. The local-agent pass already happened in `/pre-pr-review`. This skill exists for the external-reviewer + CI follow-up loop only, much lighter per tick (a few API calls, no Task spawns).

**Rule (mandatory):** When fixes are needed, fix EVERYTHING valid in this round. No "out of scope", no "pre-existing", no "too big", no "older non-touched code". The only items skipped are ones factually wrong (verified against current code, not vibes); each skip is logged in the round-history entry with the reason.

**Security alerts (CodeQL / code-scanning, Dependabot, Secret Scanning) are NEVER allowed to sit open.** Each open alert in scope for this PR must be either (a) fixed in the source code in this round, or (b) explicitly dismissed via the GitHub API with one of the sanctioned reasons (Phase 6b). Silent acceptance ("we'll get to it later", "not blocking", "third-party issue") is forbidden. The only sanctioned exits are FIX or DISMISS WITH REASON.

**Socket Security** alerts surface as PR-level review comments, not via a dedicated GitHub API. Treat them as part of the regular reviewer feedback in Phase 6 (FIX in code or, if a verified false-positive, post an `@socket-security ignore-rule <rule>` reply on the comment thread per Socket's ignore syntax). Convergence (Phase 3) does not gate on a separate Socket Security counter; the "no new comments since cached IDs" branch already covers Socket's PR-comment flow.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: resolve PR + load state

1. **PR resolution:** if `$1` is numeric, use it. Else `gh pr list --head "$(git branch --show-current)" --json number --jq '.[0].number'`. Fail clearly if none.
2. Get `OWNER/REPO` via `gh repo view --json nameWithOwner -q .nameWithOwner`.
3. Validate `OWNER/REPO` matches `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$` and PR number matches `^[0-9]+$` before any shell interpolation.
4. **State file:** `_audit/babysit-pr/<PR>.state.json`. Load if present, else default:

   ```json
   {
     "pr": N,
     "owner_repo": "OWNER/REPO",
     "self_login": "<gh api user --jq .login, cached>",
     "round": 0,
     "cadence_seconds": 900,
     "max_rounds": 24,
     "last_head_sha": "",
     "last_review_id": 0,
     "last_pr_comment_id": 0,
     "last_issue_comment_id": 0,
     "last_ci_state": "",
     "last_action_at": "<ISO-now>",
     "last_merge_attempt_headRefOid": "",
     "rate_limit_pings": 0,
     "scanners_available": {
       "code_scanning": true,
       "dependabot": true,
       "secret_scanning": true
     },
     "history": []
   }
   ```

   `self_login` is cached on the first invocation so subsequent ticks don't re-call `gh api user`. `last_ci_state` is the cached overall CI verdict (e.g. `"success"`, `"failure"`, `"pending"`) used for the Phase 5 `ci_state_change` delta. `scanners_available` starts with all three scanners optimistically true; Phase 1 flips an entry to `false` if the corresponding endpoint returns 404 / 403, and Phase 1 reads the map on subsequent ticks to skip endpoints already proven unavailable on this repo.

5. Apply `$2` / `$3` overrides if given (parse `15m` -> 900, `30m` -> 1800, plain int -> seconds; max-rounds is plain int).
6. Use `Write` (not `cat >`) to create / update the state file. Read it first if it exists (Read tool requirement).

## Phase 1: fetch current PR state (cheap, parallel)

**Hard rule -- never truncate `body` in jq queries used for triage.** No `body | .[0:N]`, no `.[0:500]`, no `head -c`. Any reviewer (bot or human) can bury actionable findings anywhere in a body that runs 50 KB or longer. The Phase 7 triage MUST see the full text. The bash batch below requests `body` verbatim; do NOT add a slice when adapting it. Bodies that look "huge" are fine -- they pass through to the working set unchanged.

**Hard rule -- no reviewer-author allowlist.** Fetch every author unfiltered (no `select(.user.login == "...")` baked into the initial fetch). Bots vary per repo (review-bots, dependency-bots, security-bots, summarisation-bots, ...) and the user can add or rotate them at any time; human reviewers can show up at any time. Categorisation by author happens in Phase 7 from the response, never via an allowlist baked into this skill.

**Hard rule -- round 1 reads every comment in full.** On the very first tick after PR creation (`state.round == 0`, cursors `last_review_id` / `last_pr_comment_id` / `last_issue_comment_id` all equal 0), every reviewer's full body across all three streams MUST be read end-to-end, not skimmed. The Phase 7 triage on round 1 builds the entire baseline working set; missing a buried finding here means the PR ships the first push with that finding still open. Subsequent rounds (`state.round >= 1`) only need the delta since the cached cursors -- Phase 5's diff-cache covers that and you can rely on it. The "read everything in full" obligation applies specifically to round 1; later rounds read only what's new.

Run in one Bash batch (parallel `&` then `wait` is fine here, or sequential since each is sub-second):

```bash
gh pr view N --json state,headRefOid,statusCheckRollup,reviewDecision,mergeable,mergedAt,headRefName
# Head commit timestamp -- needed by the Phase 3 silent-approval
# fallback to compare the rolling summary's `updated_at` against the
# moment the head was pushed.  ``commit.committer.date`` is the
# canonical "this commit landed on the branch" timestamp.
HEAD_SHA="$(gh pr view N --json headRefOid --jq .headRefOid)"
HEAD_COMMIT_TIME="$(gh api "repos/OWNER/REPO/commits/$HEAD_SHA" --jq '.commit.committer.date')"
# FULL bodies. Never truncate. ``body`` (not ``body | .[0:500]``) is
# the only acceptable form here -- truncation hides actionable
# findings buried past the cutoff (closing-line dispositions, embedded
# outside-diff-range comments, multi-paragraph human asks, ...).
# No author allowlist either: fetch every reviewer unfiltered, then
# categorise in Phase 7 from the response.
gh api repos/OWNER/REPO/pulls/N/reviews --paginate --jq '[.[] | {id, commit_id, author: .user.login, state, submitted_at, body}]'
gh api repos/OWNER/REPO/pulls/N/comments --paginate --jq '[.[] | {id, in_reply_to_id, author: .user.login, path, line, body, created_at}]'
gh api repos/OWNER/REPO/issues/N/comments --paginate --jq '[.[] | {id, author: .user.login, body, created_at, updated_at}]'
```

`mergedAt` is the right field; `merged` does not exist on `gh pr view --json` and will fail. A non-null `mergedAt` (or `state == "MERGED"`) means merged. Cap each fetch at a reasonable size; CodeRabbit review bodies can be 50KB+, that's fine.

`in_reply_to_id` is captured on the inline-comments fetch so threaded replies (e.g. CodeRabbit follow-ups, human reviewer back-and-forth, Socket Security `@socket-security ignore-rule` answers) are visible in the working set, not just the top-level comments.

**Security alerts (additional fetches in the same batch):**

```bash
HEAD_BRANCH="$(gh pr view N --json headRefName --jq .headRefName)"

# CodeQL + other code-scanning alerts visible on the PR branch.
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&ref=refs/heads/$HEAD_BRANCH&per_page=100" --paginate \
  --jq '[.[] | {number, severity: .rule.severity, rule: .rule.id, path: .most_recent_instance.location.path, line: .most_recent_instance.location.start_line, message: .most_recent_instance.message.text, html_url}]'

# Dependabot vulnerability info, scoped to the PR's actual dependency
# changes. The /dependabot/alerts endpoint is repo-wide (no ref filter
# in the REST API), which would surface issues unrelated to this PR;
# the dependency-review compare endpoint takes a base...head range and
# returns vulnerabilities introduced by the PR's manifest changes
# directly. Use this instead so the babysit loop only blocks on
# vulnerabilities the PR actually introduced or surfaced.
BASE_REF="$(gh pr view N --json baseRefName --jq .baseRefName)"
gh api "repos/OWNER/REPO/dependency-graph/compare/$BASE_REF...$HEAD_BRANCH" --paginate \
  --jq '[.[] | select(.vulnerabilities | length > 0) | {package: .name, ecosystem: .ecosystem, manifest: .manifest, change_type, vulnerabilities: [.vulnerabilities[] | {severity, advisory_ghsa_id, advisory_summary}]}]'

# Secret-scanning alerts (repo-wide).
gh api "repos/OWNER/REPO/secret-scanning/alerts?state=open&per_page=100" --paginate \
  --jq '[.[] | {number, secret_type, secret_type_display_name, html_url}]'
```

If any endpoint returns 404 (feature disabled on the repo) or 403 (insufficient token scope), log a one-line warning in the round summary, set `state.scanners_available[<scanner>] = false`, and continue. The absence of one scanner doesn't halt the loop. Subsequent ticks skip 404'd endpoints by reading `state.scanners_available`.

## Phase 2: terminal stop conditions (no reschedule)

If `state` is `MERGED` or `CLOSED`:
- Append a final history entry: `{round, action: "terminal", reason: "PR <state>"}`.
- Write state file.
- Resolve the PR's web URL via `gh pr view N --json url --jq .url` (or pull it from the JSON fetched in Phase 1 if you already requested `url` there).
- Print TWO lines, in this exact order so the URL renders as a clickable link in the user's terminal:

  ```text
  babysit-pr round R: PR #N <MERGED|CLOSED>, exiting.
  https://github.com/OWNER/REPO/pull/N
  ```

- **Do NOT** ScheduleWakeup. Loop ends.

## Phase 3: convergence check (success exit, no reschedule)

Convergence holds when ALL true:
- Every entry in `statusCheckRollup` has `conclusion` in {SUCCESS, NEUTRAL, SKIPPED} and no entry is `IN_PROGRESS` / `QUEUED` / `PENDING`.
- **CodeRabbit "no findings" signal** -- check in this order:
  1. **Rolling summary comment (primary signal).** CodeRabbit posts ONE issue comment with a leading marker `<!-- This is an auto-generated comment: summarize by coderabbit.ai -->` and *edits that same comment* on every push. Find it via `gh api repos/OWNER/REPO/issues/N/comments --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]" and (.body | startswith("<!-- This is an auto-generated comment: summarize by coderabbit.ai -->")))] | .[0]'` (sort-stable, the *first* such comment is the rolling one). Both convergence checks below are *substring* matches against the comment body -- the body has banner / details / configuration sections wrapping the relevant lines, so don't try to anchor either match to a specific position. **Convergence holds** when (a) the body contains the substring `No actionable comments were generated in the recent review. 🎉`, AND (b) the body contains a `Reviewing files that changed from the base of the PR and between <BASE_SHA> and <HEAD_SHA>.` block whose `<HEAD_SHA>` token equals the current `headRefOid`. This is the canonical "review done, nothing to fix" signal; trust it.
  2. **Per-review fallback.** If the summary comment is unavailable (very early PR, or CodeRabbit changed its banner format), accept the older signal: the most recent CodeRabbit review body contains `Actionable comments posted: 0`.
  3. **Silent-approval fallback (last resort).** If the most recent CodeRabbit review's `commit_id` is older than the current head AND the rolling summary comment is also stale (its `updated_at` predates `HEAD_COMMIT_TIME` from the Phase 1 fetch), AND the CodeRabbit `StatusContext` (`__typename: "StatusContext", context: "CodeRabbit"`) for the current head is `state: SUCCESS`, AND no rate-limit / "currently processing" / "I'll be back" markers (as defined in the Phase 4 marker table) were detected on the most recent CodeRabbit-authored item across reviews + issue comments (the same scan set Phase 4 uses) -- treat as silent approval. Used only when neither (1) nor (2) is conclusive.
- **Zero open security alerts in scope.** Scope is per-scanner (matches Phase 6b): zero open **code-scanning** alerts on the PR branch (`ref=refs/heads/$HEAD_BRANCH`), zero **Dependabot** vulnerabilities introduced/surfaced by the PR's dependency changes (via `/dependency-graph/compare/<base>...<head>`), and zero open **secret-scanning** alerts at the repository level (secret-scanning is always repo-scoped because a leaked secret is a leaked secret regardless of which PR happened to surface it). Every in-scope alert must be either fixed or explicitly dismissed via Phase 6b.
- No new reviews / inline comments / issue comments since cached IDs from any author other than `synthorg-repo-bot[bot]` or you (skip your own ping comments via Phase 4).

If converged:
- Append history `{round, action: "converged", checks_passed: N}`.
- **Squash-merge immediately, but only once per head SHA.** Convergence is not a "ready for human" handoff; the user mandate is for this skill to drive the PR all the way to `MERGED`. Compare the current `headRefOid` against `state.last_merge_attempt_headRefOid` to decide which sub-flow to enter. (Phase 11 owns clearing `state.last_merge_attempt_headRefOid` when a new commit lands; Phase 3 only reads the guard.)

  **Naming convention.** Throughout Phase 3, `headRefOid` is the in-memory variable from the Phase 1 fetch and `head_sha` is the canonical history-entry field name. They carry the same value; the two names exist only to distinguish "live PR state, just fetched" from "persisted state we wrote earlier." Every history append below MUST include `head_sha: headRefOid` so the reverse-walk lookup in sub-flow A can match entries by a single, consistent identifier. Do NOT omit `head_sha` from any append, even when the action is `merged` (the success-path entry must still carry it so a future round can confirm which head merged).

  ### Sub-flow A: same-head re-check (`headRefOid == state.last_merge_attempt_headRefOid`)

  An earlier tick already attempted this exact head. Do NOT re-issue the merge call -- the prior `--auto` request is still attached to this head and a second call would be redundant or worse.

  1. Resolve the prior outcome from `state.history` by walking entries in **reverse chronological order** (most recent first) until you find one whose `head_sha == headRefOid` AND whose `action` is one of `merge_queued` / `merge_blocked` / `merged`. Capture that entry as `prior_attempt`. If no such entry exists (e.g. state file was rewritten), treat the prior attempt as `merge_blocked` with `reason: "history lookup miss"` -- the safe default since a queued merge that lost its history record cannot be reasoned about and the user should be told.
  2. Re-fetch live state with `gh pr view N --json state,mergedAt`, then enter exactly one of these branches:

     - **`state == "MERGED"` (`mergedAt != null`):** the queued merge has fired since the previous tick. Append history `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: "MERGED"}` AND `{round, action: "merged", method: "squash", head_sha: headRefOid}`. Write state. Print the `CONVERGED + SQUASH-MERGED` line. Exit (no ScheduleWakeup).
     - **`state == "OPEN"` and `prior_attempt.action == "merge_queued"`:** the merge is still pending its required checks. Append history `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: "OPEN_queued"}`. Write state. ScheduleWakeup at the standard cadence (next tick lands in Phase 2's terminal-state branch once the auto-merge fires). Exit.
     - **`state == "OPEN"` and `prior_attempt.action == "merge_blocked"`:** the user must unblock manually before another attempt. Append history `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: "OPEN_blocked"}`. Write state. Print the `CONVERGED, merge blocked: <prior_attempt.reason>` line using the recorded reason. Exit (no ScheduleWakeup).
     - **Fallback (any other combination):** the freshly-fetched state is something the three explicit branches above did not anticipate -- e.g. `state == "CLOSED"` (PR closed without merge between ticks), `state == "OPEN"` with `prior_attempt.action == "merged"` (the head got reverted or force-pushed back), or any unexpected GraphQL state value GitHub adds in the future. Treat as blocked so the loop never silently retries. Compute `reason = "unexpected: state=<state>, prior=<prior_attempt.action or 'none'>"`. Append history `{round, action: "merge_blocked", head_sha: headRefOid, observed_state: state, reason}` AND `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: state}`. Write state. Print the `CONVERGED, merge blocked: <reason>` single-line variant. Exit (no ScheduleWakeup -- the user must investigate before any further automated attempt).

  ### Sub-flow B: fresh attempt (`headRefOid != state.last_merge_attempt_headRefOid`)

  1. Record `state.last_merge_attempt_headRefOid = headRefOid` (the value from the Phase 1 fetch) and write state BEFORE running the merge, so a crash mid-call still leaves the guard set (which sub-flow A then handles correctly on the next tick).
  2. Run the merge with stderr captured into a variable AND the exit code preserved so the branching logic below has explicit values to test. `MERGE_REASON` normalises the captured stderr into a single line of plain text (ANSI escape sequences stripped, all whitespace collapsed) so the history entry and terminal output are both legible regardless of what the underlying tool printed:

     ```bash
     MERGE_STDERR="$(gh pr merge N --squash --auto 2>&1 >/dev/null)"
     MERGE_EXIT=$?
     # Strip ANSI escape sequences (CSI, OSC, single-character SS3 etc.)
     # and collapse all whitespace runs (including embedded newlines)
     # into a single space, then trim leading/trailing whitespace.
     MERGE_REASON="$(printf '%s' "$MERGE_STDERR" \
       | sed -E 's/\x1B\[[0-9;?]*[ -\/]*[@-~]//g; s/\x1B[]PX^_].*?\x1B\\//g; s/\x1B[@-Z\\-_]//g' \
       | tr -s '[:space:]' ' ' \
       | sed -E 's/^ //; s/ $//')"
     ```

     `--auto` is harmless if branch protection is already satisfied (squashes immediately) and is the right behaviour if a final required check is still queueing (queues the merge for when checks pass).

  3. Re-fetch live state with `gh pr view N --json state,mergedAt`, then enter exactly one of these branches using the captured `MERGE_REASON` / `MERGE_EXIT` plus the freshly-fetched `state`:

     - **`state == "MERGED"` (immediate success):** append history `{round, action: "merged", method: "squash", head_sha: headRefOid}`. Write state. Print the `CONVERGED + SQUASH-MERGED` line. Exit (no ScheduleWakeup -- Phase 2's terminal exit covers any future re-entry).
     - **`state == "OPEN"` AND `MERGE_EXIT == 0` (queued):** append history `{round, action: "merge_queued", head_sha: headRefOid}`. Write state. ScheduleWakeup at the standard cadence so the next tick lands in Phase 2's terminal-state branch once the auto-merge fires. Do NOT print a terminal line yet. Exit.
     - **Otherwise (`MERGE_EXIT != 0` or `state` is neither `MERGED` nor `OPEN`):** the merge was rejected by branch protection / CODEOWNERS / merge-queue policy / etc. Append history `{round, action: "merge_blocked", head_sha: headRefOid, reason: "$MERGE_REASON"}`. Write state. Print the `CONVERGED, merge blocked: $MERGE_REASON` single-line variant. Exit (no ScheduleWakeup -- the user must unblock manually). A future push that lands a new commit will clear the guard via Phase 11 and allow a fresh attempt.

## Phase 4: CodeRabbit rate-limit dance

Inspect the most recent CodeRabbit-authored item across reviews + issue comments (`author: "coderabbitai[bot]"`). Look for these markers (case-insensitive substring):

| Marker phrase | Meaning | Action |
|---|---|---|
| `currently processing` | CodeRabbit is mid-review | No-op this tick (sleep, no ping) |
| `rate limit` / `rate-limited` / `rate limited` | CodeRabbit hit OpenRouter / OpenAI rate limit | Ping + sleep |
| `i'll be back` / `back online` / `try again later` | CodeRabbit deferred review | Ping + sleep |
| `you've reached your` / `quota` | Quota exhaustion | Ping + sleep |

**Ping action:** post `@coderabbitai review` as an issue comment via the GitHub API:

```bash
gh api "repos/$OWNER_REPO/issues/$PR/comments" -X POST -f body='@coderabbitai review'
```

Then increment `rate_limit_pings`, append history `{round, action: "rate_limit_ping", ping_count: K}`, ScheduleWakeup, exit.

**Important:** when scanning issue comments later, exclude any comment authored by `synthorg-repo-bot[bot]` OR with body exactly `@coderabbitai review` so the skill doesn't trip on its own pings.

There is NO upper bound on `rate_limit_pings`. The user explicitly said 10x with 15min delay is fine. The only stop is `max_rounds`.

## Phase 5: diff cache, did anything actually change?

Compute deltas vs. cached IDs:

- `new_commits` = current `headRefOid` != `state.last_head_sha`
- `new_reviews` = `max(review.id) > state.last_review_id` AND review author is not self/synthorg-repo-bot
- `new_pr_comments` = `max(pr_comment.id) > state.last_pr_comment_id` AND author is not self
- `new_issue_comments` = `max(issue_comment.id) > state.last_issue_comment_id` AND comment is not self-authored AND body is not `@coderabbitai review` (our own pings)
- `ci_state_change` = current overall CI state differs from cached state

If NONE of these AND no rate-limit dance fired in Phase 4:
- `state.last_action_at = <ISO-now>` (heartbeat)
- Append history `{round, action: "noop"}`
- Write state. Print: `babysit-pr round R: no changes, sleeping <cadence>m.`
- ScheduleWakeup, exit.

## Phase 6: collect actionable feedback

Build the working set:

- **CI failures:** for each entry in `statusCheckRollup` with `conclusion: FAILURE`, capture `name` and the `targetUrl`. The `targetUrl` is the link to the failed job on github.com and embeds the run id (e.g. `https://github.com/OWNER/REPO/actions/runs/<RUN_ID>/job/<JOB_ID>`). Extract the run id from that URL, then pull the failed-job logs:

  ```bash
  # statusCheckRollup gives us the per-check targetUrl; iterate and extract.
  gh pr view N --json statusCheckRollup --jq '.statusCheckRollup[] | select(.conclusion == "FAILURE") | {name, targetUrl}' \
    | while read -r row; do
        TARGET_URL="$(printf '%s' "$row" | jq -r .targetUrl)"
        RUN_ID="$(printf '%s' "$TARGET_URL" | sed -n 's#.*actions/runs/\([0-9]\{1,\}\).*#\1#p')"
        [ -z "$RUN_ID" ] && continue
        gh run view "$RUN_ID" --log-failed
      done
  ```

  If `targetUrl` is missing on an entry (rare; happens with status-check rollups that aren't workflow runs, e.g. external GitHub Apps), surface the check name and conclusion in the round summary and skip log collection for that entry rather than blocking the whole tick.
- **New review submissions:** every review with `id > last_review_id` (excluding self/bot). Parse review body for embedded outside-diff-range comments (CodeRabbit puts them in `<details>` blocks at the top, same parser as `/aurelio-review-pr` Phase 4).
- **New inline comments:** every PR comment with `id > last_pr_comment_id` (excluding self).
- **New issue comments:** every issue comment with `id > last_issue_comment_id` (excluding self + `@coderabbitai review` pings).

## Phase 6b: security-alert triage (FIX or DISMISS, never leave open)

Build a separate working set from the three scanner fetches in Phase 1. Scope per scanner:

- **code-scanning** (CodeQL etc.): alerts visible on the PR branch (`ref=refs/heads/$HEAD_BRANCH`), filtered by Phase 1's fetch.
- **Dependabot**: vulnerabilities the PR's manifest changes introduce or surface, returned by the `/dependency-graph/compare/<base>...<head>` endpoint. Anything pre-existing-and-unchanged at the dependency level is out of scope for this PR's babysit (it would block every unrelated PR otherwise).
- **secret-scanning**: alerts on the repo. Secret-scanning has no per-PR or per-branch filter, so every open secret alert is treated as in scope; the whole point of secret scanning is that any leaked secret needs immediate handling regardless of which PR happened to surface it.

For each in-scope alert:

1. **Read the cited file:line** to confirm the alert applies to current code (not stale from a removed line).
2. **Decide**: FIX (in-code) or DISMISS (via API with reason). There is no third option. "Pre-existing", "not blocking", "third-party advisory", "we'll address later" all map to either FIX or DISMISS, pick one.
3. If FIX: add to the working set for Phase 8.
4. If DISMISS: post via the appropriate PATCH endpoint with the appropriate reason vocabulary:

   **Code-scanning** (CodeQL etc.) via `PATCH /repos/OWNER/REPO/code-scanning/alerts/<number>`:

   ```bash
   gh api -X PATCH "repos/OWNER/REPO/code-scanning/alerts/$NUMBER" \
     -f state=dismissed -f dismissed_reason="<REASON>" -f dismissed_comment="<one-line justification>"
   ```

   Allowed `dismissed_reason` values: `false positive`, `won't fix`, `used in tests`. Use `false positive` only when the rule misfired (verify with the cited file open). Use `used in tests` only for findings inside `tests/` or test fixtures. Use `won't fix` for accepted risk (and document the acceptance in the `dismissed_comment` AND in a code comment at the cited line so future readers see the trail).

   **Dependabot security** via `PATCH /repos/OWNER/REPO/dependabot/alerts/<number>`:

   ```bash
   gh api -X PATCH "repos/OWNER/REPO/dependabot/alerts/$NUMBER" \
     -f state=dismissed -f dismissed_reason="<REASON>" -f dismissed_comment="<one-line justification>"
   ```

   Allowed `dismissed_reason` values: `fix_started`, `inaccurate`, `no_bandwidth`, `not_used`, `tolerable_risk`. Prefer `not_used` (transitive dep of test-only / dev-only path) and `inaccurate` (advisory doesn't actually apply). Never use `no_bandwidth` (that's just deferral and the user has banned it).

   **Secret scanning** via `PATCH /repos/OWNER/REPO/secret-scanning/alerts/<number>`:

   ```bash
   gh api -X PATCH "repos/OWNER/REPO/secret-scanning/alerts/$NUMBER" \
     -f state=resolved -f resolution="<REASON>" -f resolution_comment="<one-line justification>"
   ```

   Allowed `resolution` values: `false_positive`, `wont_fix`, `revoked`, `used_in_tests`. If `revoked`, the secret MUST actually have been rotated before dismissal. Verify with the relevant secret backend before issuing the PATCH.

   **Socket Security** is handled in Phase 6 (regular reviewer-comment triage), not here. It does not have a dedicated dismiss-via-API endpoint; the comment-thread reply `@socket-security ignore-rule <rule>` is the only suppression path.

5. **Record every dismissal** in the round-history entry: `{round, action: "alert_dismissed", scanner, alert_number, reason, justification}`. Dismissals are auditable; never make one without the entry.

6. If even one alert was dismissed under `won't fix` / `tolerable_risk`, also leave a code comment at the cited file:line referencing the alert number so future readers know the risk was accepted and where to find the rationale.

After this phase, security alerts are either queued for Phase 8 fixes or already dismissed. No alert sits open across rounds without a recorded action.

## Phase 7: triage (no approval gate)

Build a triage table inline (in the chat output, not a file). Columns: `Source | Severity | File:Line | Issue | Valid?`.

For each item:
1. **Source** = author + comment type (e.g. `coderabbitai/inline`, `copilot/review`, `human:OctoCat/issue`, `CI:test-go-1.26`).
2. **Severity** = mapped from reviewer labels (CodeRabbit uses `Issue` / `Suggestion` / `Refactor` / `Security`; map `Issue` to MAJOR, `Security` to CRITICAL, others to MEDIUM by default; CI failures = CRITICAL).
3. **Valid?** = your assessment against the current code state. Read the cited file:line BEFORE classifying. An invalid finding requires a one-line reason recorded in the round-history entry.

**Mandatory:** every valid finding gets fixed in this round, regardless of scope, age, or size. The only skips are factually wrong findings, and each one is logged with the disproof.

Print the table. Skip `AskUserQuestion`. Proceed straight to fixes.

## Phase 8: implement fixes

Group findings by file. For each file:
1. Read the file (full file, not just the cited region).
2. Apply each fix that touches this file.
3. Move to the next file.

If a fix changes test expectations: update the test in the same round. If a fix introduces a new code path: add coverage in the same round.

## Phase 9: verify locally

Conditional gates by file type touched:

- **Python** (`.py` in `src/` or `tests/`):

  ```bash
  uv run ruff check src/ tests/ --fix
  uv run ruff format src/ tests/
  uv run mypy src/ tests/
  uv run python -m pytest tests/ -m unit -n 8
  ```

- **Web** (`.tsx`/`.ts`/`.css` in `web/src/`):

  ```bash
  npm --prefix web run lint
  npm --prefix web run type-check
  npm --prefix web run test
  ```

- **Go** (`.go` in `cli/`):

  ```bash
  go -C cli vet ./...
  go -C cli test ./...
  go -C cli build ./...
  ```

Failure handling: if a gate fails, fix the failure in this round (don't push broken code). If you can't, surface it via `AskUserQuestion` and pause the loop.

## Phase 9b: pre-push completeness sweep (mandatory before EVERY push)

Before staging or committing, re-run the Phase 1 fetches one more time to catch comments / reviews / alerts that landed in the **race window between Phase 1 (fetch) and Phase 9 (post-fix verification)** of this same tick. A single Phase 8 + Phase 9 cycle can take 5-15 minutes; reviewer bots typically retry on a 30-second to 2-minute cadence; human reviewers are unbounded. A comment that arrives during that race window MUST land in this push, not the next one -- otherwise the loop ships a known-stale view, the next tick re-runs the same fix cycle, and the PR thrashes.

Applies to every push. The race-window risk is identical on round 1 and round 2+; only the **baseline cursor** for "what counts as new" differs:

- **Round 1**: cursors are 0, so the Phase 1 working set IS the entire reviewer history; the sweep is checking whether anything appeared after that initial paginated read.
- **Round 2+**: cursors carry the IDs from the previous tick; the Phase 1 working set is the delta since last push; the sweep is checking whether anything appeared after that delta-fetch.

The sweep mechanic is uniform. The only thing that varies between rounds is the cursor baseline, which Phase 5's diff-cache already manages.

Steps:

1. **Re-fetch reviews / inline comments / issue comments / security alerts** with the same queries as Phase 1. Use `body` verbatim (no truncation) and no author allowlist; same hard rules as the initial fetch.

2. **Diff against the working set** Phase 7 was triaged from. Compute:

   - `new_reviews_since_phase1` = reviews whose `id` is greater than the maximum review id captured at Phase 1 fetch time.
   - `new_inline_comments_since_phase1` = inline comments with id greater than the Phase 1 maximum.
   - `new_issue_comments_since_phase1` = issue comments with id greater than the Phase 1 maximum, excluding self-pings (the same exclusions Phase 5 uses).
   - `new_security_alerts_since_phase1` = open alerts (per scanner) whose `number` is not in the Phase 1 set.

   Self-authored items (the cached `state.self_login` from Phase 0) and items the loop posted itself (e.g. rate-limit pings) are excluded the same way Phase 5 / Phase 6 exclude them. Bot items are NOT excluded -- bots are first-class reviewers.

3. **Author roster verification.** Build a set of `(author_login, item_type)` tuples across the re-fetched data. Print this set as a one-line summary in the chat output so the operator can see which authors were considered before the push lands (e.g. `pre-push roster: [(<bot-A>, inline), (<bot-B>, review), (<human-X>, review), ...]`; do not hardcode names). If any author appears that was NOT in the Phase 1 roster, that's a signal new feedback arrived; treat it as new findings even if no specific item id grew (e.g. a reviewer dismissed and resubmitted).

4. **If anything new is in scope:** loop back to Phase 7 (triage) with the additional items folded into the working set, then Phase 8 (fix), then Phase 9 (verify), then re-enter Phase 9b. Do NOT advance to Phase 10 with newly-arrived feedback unaddressed -- that's the exact failure mode this phase exists to prevent.

5. **If nothing new arrived:** proceed to Phase 10. Append history `{round, action: "pre_push_sweep_clean", checked_at: <ISO-now>, authors: [...]}` so the audit trail records that the sweep ran.

6. **Iteration cap.** If Phase 9b loops more than 3 times in a single round (i.e. every fix attempt races a new comment), stop and `AskUserQuestion`: "Pre-push sweep has loop-bounced 3 times on PR #N; reviewer is posting faster than fixes ship. Push current batch / wait / pause loop?" The user picks. This prevents pathological live-review situations from blocking the loop indefinitely.

The sweep is read-only -- no API mutations, no commits, no pushes -- so it costs only the API budget of the four `gh api` calls already familiar from Phase 1. Time budget on a quiet PR: under 5 seconds.

## Phase 10: commit + push

1. `git add -A`
2. Commit with message `fix: babysit round R, M findings (X coderabbit, Y copilot, Z ci)` plus a body listing the fixed items.
3. `git push` (no `-u`; branch already tracks).
4. Hook failures: fix the actual issue, never `--no-verify`, never `--amend`. Create a NEW commit if needed.

## Phase 11: update state, schedule next tick

1. Update `state.json`. The variable `headRefOid` here refers to the value fetched in Phase 1 (`gh pr view N --json headRefOid`), the same identifier Phase 3 reads:
   - `round += 1`
   - **If `headRefOid != state.last_head_sha`:** clear `state.last_merge_attempt_headRefOid = ""` so the next time Phase 3 reaches convergence on this branch, the merge guard does not block a fresh attempt against the new head. Without this reset the merge would only ever fire once per babysit lifetime, regardless of how many later commits land. Do this BEFORE updating `last_head_sha` so the comparison is against the previous tick's value.
   - `last_head_sha = headRefOid`
   - `last_review_id = max(review.id, last_review_id)` (same for the two comment streams)
   - `last_action_at = <ISO-now>`
   - Append history `{round, action: "fixed_and_pushed", findings: M, sources: {...}}`
2. **Max-rounds check:** if `round >= max_rounds`:
   - `AskUserQuestion`: "babysit-pr hit round R/max_rounds on PR #N. Continue / stop / raise cap?"
   - On "continue": apply the user's new cap, reschedule.
   - On "stop": write state and exit (no reschedule).
   - On "raise cap": apply the new value (Other -> integer), reschedule.
3. **Reschedule:**

   ```text
   ScheduleWakeup({
     delaySeconds: state.cadence_seconds,
     prompt: "/babysit-pr <PR>",
     reason: "round R pushed M fixes; next tick checks for CodeRabbit re-review + CI"
   })
   ```

## Output discipline (per tick)

Print exactly ONE concise status line per tick at the end. Mid-loop verdicts (single line):

- `babysit-pr round R: no changes, sleeping <C>m.`
- `babysit-pr round R: rate-limit ping #K sent to CodeRabbit, sleeping <C>m.`
- `babysit-pr round R: M findings fixed and pushed, sleeping <C>m.`
- `babysit-pr round R: paused at max-rounds, awaiting decision.`

Terminal verdicts (loop-exit cases for Phase 2 / Phase 3) print a status line followed by the PR's web URL on its own line so the user can click straight through to the PR. Both lines together:

- Convergence + auto-merge succeeded:

  ```text
  babysit-pr round R: CONVERGED + SQUASH-MERGED. Done.
  https://github.com/OWNER/REPO/pull/N
  ```

- Convergence reached but the merge was blocked (e.g. branch protection requires a human approval):

  ```text
  babysit-pr round R: CONVERGED, merge blocked: <reason>.
  https://github.com/OWNER/REPO/pull/N
  ```

- PR already in a terminal state on entry:

  ```text
  babysit-pr round R: PR #N <MERGED|CLOSED>, exiting.
  https://github.com/OWNER/REPO/pull/N
  ```

The URL is fetched from the PR JSON (`gh pr view N --json url --jq .url`) and printed verbatim. Do not wrap it in Markdown link syntax; terminals auto-detect bare https:// URLs and make them clickable, while explicit `[text](url)` links render as literal characters in plain-terminal contexts.

Render the full triage table only when there's something to fix.

---

## Rules

### Loop discipline

- **Never invoke `/aurelio-review-pr` or any Task agent.** This is a watchdog, not a re-reviewer.
- **One push per round.** Bundle CI fixes + reviewer fixes + security-alert fixes + alert dismissals into a single commit. Multiple pushes burn CodeRabbit re-review rate limits and fragment threads. (`feedback_push_and_review_discipline.md` §4.)
- **Check CI and external reviewers TOGETHER every cycle.** Never push a CodeRabbit-only fix and leave CI red, or vice versa. (`feedback_push_and_review_discipline.md` §5.)
- **Default cadence is 900s (15 min).** CodeRabbit usually re-reviews within 5 to 10 min of a push. (`feedback_push_and_review_discipline.md` §7.)
- **Default push immediately after committing.** No "ready to push?" prompt. (`feedback_push_and_review_discipline.md` §1.)

### Completeness, the only sanctioned exits

- **Fix EVERYTHING valid.** Out-of-scope, pre-existing, larger work, older non-touched code, all in scope. The user's mandate.
- **Security alerts: FIX or DISMISS, never leave open.** Phase 6b. Open alerts across rounds = workflow failure.
- **Never push incomplete work.** Before every push verify: all changes committed, lint passes, tests pass, schema drift zero (`atlas schema diff` if touching persistence), no pending TODO from this round. (`feedback_completeness.md` §1.)
- **Never silently narrow scope.** If a fix turns out to be much bigger than the finding suggested (cascading edits across many files, schema change, migration), pause and `AskUserQuestion` with the new info before shipping a partial fix. (`feedback_completeness.md` §3.)
- **Never skip flaky tests.** If a CI failure is a flaky test, the fix is to make it deterministic (mock `time.monotonic()` / `asyncio.sleep()`, eliminate the race), NOT to mark it skip / xfail / "pre-existing flaky". Flaky-test fixes ride in this round. (`feedback_completeness.md` §5.)
- **Skipped items must be factually wrong.** Each skip is logged in round-history with the concrete disproof (file:line evidence that the finding doesn't apply).
- **Never silently dismiss a security alert.** Phase 6b dismissals always carry a `dismissed_comment` AND a round-history entry.

### External reviewer hygiene

- **Fetch ALL reviewers unfiltered.** Don't `select(.user.login == "coderabbitai[bot]")` in the initial fetch. Bots vary per repo (CodeRabbit, Gemini, Copilot, Greptile, Socket Security, ...) and human reviewers can show up at any time. Categorize by author from the response, never by an allowlist baked into this skill.
- **Stale duplicate comments are artifacts.** When CodeRabbit re-posts a finding on already-fixed code (because its index was stale at review time), verify the fix exists in the current code, post `@coderabbitai resolve` on the thread, and move on. Don't re-implement. Log as `{action: "stale_duplicate_resolved", thread_id, evidence}` in history.
- **Self-pings:** when scanning issue comments, exclude any with body exactly `@coderabbitai review` so the skill doesn't mistake its own pings for new feedback.
- **Self-comments:** when scanning reviews and inline comments, exclude `synthorg-repo-bot[bot]` and your own GitHub username (resolve via `gh api user --jq .login` once and cache in `state.self_login`).
- **Outside-diff-range comments:** CodeRabbit embeds these in `<details>` blocks at the top of the review body when the affected lines are outside the diff. Parse them as actionable inline comments. They're NOT optional. (Same parser as `/aurelio-review-pr` Phase 4.)

### Mechanics

- **Never `durable: true`** on any cron primitive. Session-only. (`feedback_no_cloud_schedule.md`.)
- **Never offer cloud `/schedule`.** Default `/loop`-style scheduling is `ScheduleWakeup`. (`feedback_no_cloud_schedule.md`.)
- **Never `--no-verify`, never `--amend`.** Hook failures get fixed in a NEW commit, never bypassed.
- **Never `--force` / `--force-with-lease`** unless the immediately-prior step actually rewrote history. This skill never rewrites history.
- **Never push when local verification (Phase 9) failed.** Hold the work, fix the failure first.
- **NEVER use `cd` in Bash commands.** Use `go -C cli`, `npm --prefix web`, absolute paths, or `bash -c "cd <dir> && <cmd>"` for tools without a `-C` equivalent. (Project bash hook enforces this.)
- **Always `uv run python -m pytest`** (not bare `pytest`) on Windows; the bare form has path issues.
- **Always `-n 8`** when running pytest locally (project hook enforces).
