---
description: "Watch a PR after creation. Polls CI + external reviewer state + open code-scanning/Dependabot/secret-scanning alerts, auto-fixes valid feedback (one push per round), dismisses justified security alerts via API with reason, handles CodeRabbit rate-limit by reposting `@coderabbitai review`, runs until convergence or merged. No local-agent invocation, no approval gate."
argument-hint: "[PR# or blank] [cadence default 15m] [max-rounds default 12]"
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

**First-tick semantics:** On a fresh state file (`last_review_id == 0` and the other ID fields at 0), every existing review / inline comment / issue comment counts as "new" relative to the cached IDs. That's intentional: the loop's first invocation against an already-active PR must triage everything that has piled up before babysit started watching, not just deltas going forward. CodeRabbit-mid-processing is the typical first-tick state on a freshly-opened PR; the right response is to wait (Phase 4 no-op) so the *next* tick batches CodeRabbit's findings together with any earlier reviewers (Gemini, Copilot, Greptile, human reviewers) into one push. The cached-ID hygiene rule below is what makes that batching work.

**Cached-ID hygiene (CRITICAL):** `last_review_id`, `last_pr_comment_id`, and `last_issue_comment_id` advance ONLY in Phase 11, after Phase 8 fixes have been pushed. Phase 4 early-exits (rate-limit dance, "currently processing") and Phase 5 no-ops MUST NOT bump these IDs; if they did, items not yet triaged would silently fall out of scope on the next tick. `last_head_sha` is a separate concern and may advance on every tick (it tracks "what commit have we seen", not "what feedback have we processed"). `round` increments at the END of every tick (success path or otherwise) so the printed round number monotonically counts wakeups.

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
     "max_rounds": 12,
     "last_head_sha": "",
     "last_review_id": 0,
     "last_pr_comment_id": 0,
     "last_issue_comment_id": 0,
     "last_ci_state": "",
     "last_action_at": "<ISO-now>",
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

Run in one Bash batch (parallel `&` then `wait` is fine here, or sequential since each is sub-second):

```bash
gh pr view N --json state,headRefOid,statusCheckRollup,reviewDecision,mergeable,mergedAt,headRefName
# Head commit timestamp -- needed by the Phase 3 silent-approval
# fallback to compare the rolling summary's `updated_at` against the
# moment the head was pushed.  ``commit.committer.date`` is the
# canonical "this commit landed on the branch" timestamp.
HEAD_SHA="$(gh pr view N --json headRefOid --jq .headRefOid)"
HEAD_COMMIT_TIME="$(gh api "repos/OWNER/REPO/commits/$HEAD_SHA" --jq '.commit.committer.date')"
gh api repos/OWNER/REPO/pulls/N/reviews --paginate --jq '[.[] | {id, commit_id, author: .user.login, state, submitted_at, body}]'
gh api repos/OWNER/REPO/pulls/N/comments --paginate --jq '[.[] | {id, author: .user.login, path, line, body, created_at}]'
gh api repos/OWNER/REPO/issues/N/comments --paginate --jq '[.[] | {id, author: .user.login, body, created_at, updated_at}]'
```

`mergedAt` is the right field; `merged` does not exist on `gh pr view --json` and will fail. A non-null `mergedAt` (or `state == "MERGED"`) means merged. Cap each fetch at a reasonable size; CodeRabbit review bodies can be 50KB+, that's fine.

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
- Write state.
- Resolve the PR's web URL via `gh pr view N --json url --jq .url` (or pull it from the JSON fetched in Phase 1 if you already requested `url` there).
- Print TWO lines, in this exact order so the URL renders as a clickable link in the user's terminal:

  ```text
  babysit-pr round R: CONVERGED (CI green, 0 actionable, no new feedback). Ready for human review/merge.
  https://github.com/OWNER/REPO/pull/N
  ```

- **Do NOT** ScheduleWakeup.

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

Then increment `rate_limit_pings`, increment `round`, append history `{round, action: "rate_limit_ping", ping_count: K}`, write state, ScheduleWakeup, exit.

**No-op exit (`currently processing`):** increment `round`, append history `{round, action: "coderabbit_processing", reviewers_seen: [...]}`, write state, ScheduleWakeup, exit. The marker reflects an in-flight CodeRabbit review that will land in 5 to 10 minutes; pinging would just fight CodeRabbit's own scheduler.

**MUST NOT update `last_review_id` / `last_pr_comment_id` / `last_issue_comment_id` on either exit path.** Those IDs only advance in Phase 11 after fixes have been pushed. If a Phase 4 exit bumped them, the next tick would treat any reviewer feedback that arrived before the ping as already-processed and silently drop it. The whole point of waiting for CodeRabbit is to batch its findings with already-pending reviewer feedback into one push the *next* round.

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
- `state.last_head_sha = current_head_sha` (track latest seen commit; safe to advance on noops since it gates new-commit detection, not feedback triage)
- Increment `round`.
- Append history `{round, action: "noop"}`.
- **MUST NOT update `last_review_id` / `last_pr_comment_id` / `last_issue_comment_id`.** A noop means we observed feedback but didn't triage it; bumping the IDs here would lose the items on the next tick.
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

## Phase 10: commit + push

1. `git add -A`
2. Commit with message `fix: babysit round R, M findings (X coderabbit, Y copilot, Z ci)` plus a body listing the fixed items.
3. `git push` (no `-u`; branch already tracks).
4. Hook failures: fix the actual issue, never `--no-verify`, never `--amend`. Create a NEW commit if needed.

## Phase 11: update state, schedule next tick

1. Update `state.json` (success path: fixes triaged AND pushed, OR convergence reached but PR still open):
   - `round += 1`
   - `last_head_sha = current_head_sha`
   - `last_review_id = max(review.id, last_review_id)` (same for the two comment streams). Bumped **only here**, never in Phase 4 / Phase 5 / Phase 6b dismissals.
   - `last_ci_state = current_ci_state`
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

- ```text
  babysit-pr round R: CONVERGED (CI green, 0 actionable, no new feedback). Ready for human review/merge.
  https://github.com/OWNER/REPO/pull/N
  ```

- ```text
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
