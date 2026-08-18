---
description: "Watch a PR after creation. Polls CI + external reviewer state + open code-scanning/Dependabot/secret-scanning alerts, auto-fixes valid feedback (one push per round), dismisses justified security alerts via API with reason, handles CodeRabbit rate-limit by reposting `@coderabbitai review`, runs until convergence or merged. No local-agent invocation, no approval gate."
argument-hint: "[PR# or blank] [cadence default 5m] [max-rounds default 24]"
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
     "cadence_seconds": 300,
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

5. Apply `$2` / `$3` overrides if given (parse `5m` -> 300, `15m` -> 900, `30m` -> 1800, plain int -> seconds; max-rounds is plain int).
6. Use `Write` (not `cat >`) to create / update the state file. Read it first if it exists (Read tool requirement).

## Phase 1: fetch current PR state (cheap, parallel)

**Hard rule -- never truncate `body` in jq queries used for triage.** No `body | .[0:N]`, no `.[0:500]`, no `head -c`. Any reviewer (bot or human) can bury actionable findings anywhere in a body that runs 50 KB or longer. The Phase 7 triage MUST see the full text. The bash batch below requests `body` verbatim; do NOT add a slice when adapting it. Bodies that look "huge" are fine -- they pass through to the working set unchanged.

**Hard rule -- no reviewer-author allowlist.** Fetch every author unfiltered (no `select(.user.login == "...")` baked into the initial fetch). Bots vary per repo (review-bots, dependency-bots, security-bots, summarisation-bots, ...) and the user can add or rotate them at any time; human reviewers can show up at any time. Categorisation by author happens in Phase 7 from the response, never via an allowlist baked into this skill.

**Hard rule -- round 1 reads every comment in full.** On the very first tick after PR creation (`state.round == 0`, cursors `last_review_id` / `last_pr_comment_id` / `last_issue_comment_id` all equal 0), every reviewer's full body across all three streams MUST be read end-to-end, not skimmed. The Phase 7 triage on round 1 builds the entire baseline working set; missing a buried finding here means the PR ships the first push with that finding still open. Subsequent rounds (`state.round >= 1`) only need the delta since the cached cursors -- Phase 5's diff-cache covers that and you can rely on it. The "read everything in full" obligation applies specifically to round 1; later rounds read only what's new.

Run in one Bash batch (parallel `&` then `wait` is fine here, or sequential since each is sub-second):

```bash
# ONE snapshot, captured once and destructured. Every later value comes
# out of `$PR_JSON`, never from a fresh `gh pr view`: a PR is live, so
# separate calls observe separate moments, and a head that moves between
# them yields a `HEAD_SHA` and a `headRefName` describing different
# states of the branch. Phase 10 builds a refspec and a lease out of
# exactly those two, so an inconsistent pair is not a cosmetic problem.
PR_JSON="$(gh pr view N --json state,headRefOid,statusCheckRollup,reviewDecision,mergeable,mergedAt,headRefName,baseRefName,baseRefOid,headRepositoryOwner,headRepository)"
HEAD_SHA="$(printf '%s' "$PR_JSON" | jq -r .headRefOid)"
HEAD_BRANCH="$(printf '%s' "$PR_JSON" | jq -r .headRefName)"
BASE_REF="$(printf '%s' "$PR_JSON" | jq -r .baseRefName)"
BASE_SHA="$(printf '%s' "$PR_JSON" | jq -r .baseRefOid)"
# The repository's default branch, which `gh pr view` does not carry.
# Phase 4 routes on "does this PR target the default branch", so both
# halves of that comparison have to be in Phase 1 state: `baseRefName`
# above, and this. Fetching either later, inside a block that a
# disabled scanner or an early exit can skip, leaves the stacked-PR row
# undecidable exactly when it matters.
DEFAULT_BRANCH="$(gh repo view OWNER/REPO --json defaultBranchRef --jq .defaultBranchRef.name)"
# Head commit timestamp -- needed by the Phase 3 silent-approval
# fallback to compare the rolling summary's `updated_at` against the
# moment the head was pushed.  ``commit.committer.date`` is the
# canonical "this commit landed on the branch" timestamp.
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
# `HEAD_BRANCH`, `BASE_REF`, `BASE_SHA` and `HEAD_SHA` all come from the
# single `$PR_JSON` snapshot destructured above; do not re-query them.

# CodeQL + other code-scanning alerts visible on the PR. CodeQL's
# pull-request workflow attaches alerts to ``refs/pull/<N>/head``,
# NOT ``refs/heads/<HEAD_BRANCH>`` -- the branch ref only sees alerts
# from a workflow that runs on `push` events to the branch (typically
# the default-branch CodeQL job, not the PR-scoped one). Query BOTH
# refs and union the results so PR-scoped alerts are not invisible to
# the babysit loop. Without this union, alert-card inline review
# comments from `github-advanced-security[bot]` (which link to
# `/security/code-scanning/<N>`) silently bypass the gate even though
# the underlying alert is open.
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&ref=refs/heads/$HEAD_BRANCH&per_page=100" --paginate \
  --jq '[.[] | {number, severity: .rule.severity, rule: .rule.id, path: .most_recent_instance.location.path, line: .most_recent_instance.location.start_line, message: .most_recent_instance.message.text, html_url, ref: .most_recent_instance.ref}]'
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&ref=refs/pull/$N/head&per_page=100" --paginate \
  --jq '[.[] | {number, severity: .rule.severity, rule: .rule.id, path: .most_recent_instance.location.path, line: .most_recent_instance.location.start_line, message: .most_recent_instance.message.text, html_url, ref: .most_recent_instance.ref}]'

# Dependabot vulnerability info, scoped to the PR's actual dependency
# changes. The /dependabot/alerts endpoint is repo-wide (no ref filter
# in the REST API), which would surface issues unrelated to this PR;
# the dependency-review compare endpoint takes a base...head range and
# returns vulnerabilities introduced by the PR's manifest changes
# directly. Use this instead so the babysit loop only blocks on
# vulnerabilities the PR actually introduced or surfaced.
# BOTH sides are commit SHAs, not branch names. The head side must be,
# because this endpoint resolves revisions inside OWNER/REPO and a fork
# PR's head branch does not exist there: it would 404, or worse, silently
# resolve a same-named branch that happens to exist in the base repo and
# report that branch's dependencies as this PR's. The base side is a SHA
# for a second reason: a ref name goes into a URL path, and a base such as
# `release/1.2` or one carrying `#` or a space is not a safe path
# component. Percent-encoding it is the wrong repair -- encoding `/` as
# `%2F` breaks GitHub's own path matching, and leaving `/` raw still
# leaves `#` and space broken -- whereas a 40-character hex OID contains
# nothing that needs encoding at all.
gh api "repos/OWNER/REPO/dependency-graph/compare/$BASE_SHA...$HEAD_SHA" --paginate \
  --jq '[.[] | select(.vulnerabilities | length > 0) | {package: .name, ecosystem: .ecosystem, manifest: .manifest, change_type, vulnerabilities: [.vulnerabilities[] | {severity, advisory_ghsa_id, advisory_summary}]}]'

# Secret-scanning alerts (repo-wide).
gh api "repos/OWNER/REPO/secret-scanning/alerts?state=open&per_page=100" --paginate \
  --jq '[.[] | {number, secret_type, secret_type_display_name, html_url}]'
```

If any endpoint returns 404 (feature disabled on the repo) or 403 (insufficient token scope), log a one-line warning in the round summary, set `state.scanners_available[<scanner>] = false`, and continue. The absence of one scanner doesn't halt the loop. Subsequent ticks skip 404'd endpoints by reading `state.scanners_available`.

**But "the scanner is not available here" and "this scan did not run" are different facts, and only the first is safe to continue past.** The two arrive as the same status code: the dependency-graph compare answers 403/404 both when Advanced Security is off for the repository and when it cannot resolve a revision it was handed. Filing the second as "scanner unavailable" converts a scan that never executed into a silent zero-vulnerability result, and the loop then reports the PR clean on evidence it does not have, which is the fail-open this skill exists to refuse.

Disambiguate before deciding, using a fact the status code does not carry: **do the revisions resolve?** Ask `gh api "repos/OWNER/REPO/commits/$BASE_SHA"` and the same for `$HEAD_SHA`.

- Both resolve and the compare still fails: the repository genuinely does not serve this endpoint. Mark the scanner unavailable and continue, as above.
- Either does not resolve: the compare was handed a revision this repository cannot see, so **no scan happened**. Do NOT mark the scanner unavailable. Record `{round, action: "scan_failed", scanner: "dependabot", detail: <status and the unresolvable OID>}`, and treat it as blocking for Phase 3: a round that could not scan has not shown the PR to be clean, so convergence must not be declared on it.

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
- **Zero open security alerts in scope.** Scope is per-scanner (matches Phase 6b): zero open **code-scanning** alerts visible on EITHER `ref=refs/heads/$HEAD_BRANCH` OR `ref=refs/pull/$N/head` (CodeQL's PR workflow uses the latter; the branch ref only sees alerts from workflows that run on `push` events), zero **Dependabot** vulnerabilities introduced/surfaced by the PR's dependency changes (via `/dependency-graph/compare/<base>...<head>`), and zero open **secret-scanning** alerts at the repository level (secret-scanning is always repo-scoped because a leaked secret is a leaked secret regardless of which PR happened to surface it). Every in-scope alert must be either fixed or explicitly dismissed via Phase 6b.
- **Every in-scope scanner actually ran.** A scanner Phase 1 recorded as `scan_failed` blocks convergence until a later tick scans successfully. Zero alerts from a scan that did not execute is not evidence of zero alerts, and this is the one place where treating the two alike would ship an unscanned PR as clean. A scanner marked genuinely unavailable is different and does not block, because "this repository does not serve that endpoint" is a fact about the repository rather than a gap in this round's evidence.
- No new reviews / inline comments / issue comments since cached IDs from any author other than `synthorg-repo-bot[bot]` or you (skip your own ping comments via Phase 4). **Evaluate this over EVERY review with `id > last_review_id`, not the highest-id review or `reviewDecision` alone:** CodeRabbit posts a `COMMENTED` review (outside-diff findings) immediately followed by an empty `APPROVED` review on the same head, so a max-id-only or `reviewDecision == APPROVED` check reads as "converged" while actionable findings sit unread in the lower-id `COMMENTED` review (see the Phase 6 caution). Open each review body before declaring convergence.

### Phase 3a: pre-merge freshness gate (MANDATORY immediately before EVERY merge call)

**NEVER merge while a review is in progress, and never merge on convergence evidence that was gathered earlier in the tick.**

Every bullet above is computed from the Phase 1 snapshot. The merge call can fire minutes or hours after that snapshot: a tick may wait on CI, run a fix cycle, or block on a monitor before reaching this point. A reviewer writing a review during that gap is the ordinary case, not the exotic one -- a review takes minutes to produce and lands whenever it lands. So convergence is a *precondition*, never a *permission slip*: it says the PR looked ready when it was measured, and only this gate says it is still ready now.

This is the merge-side twin of Phase 9b. Phase 9b exists because a push must not ship a stale view; this exists for the same reason and higher stakes, because a push is corrected by the next push and a merge is not. Applying the sweep to pushes and skipping it before the merge protects every reversible action and leaves the single irreversible one unguarded, which is exactly backwards. **A real merge on PR #2786 landed 78 seconds after a `CHANGES_REQUESTED` review carrying 7 findings -- 4 of them regressions introduced by that very round, including a security one that silently dropped a configured gVisor runtime -- because the tick verified CI immediately before merging and did not re-read the review streams.**

Run this immediately before the merge call, and re-run it if ANYTHING intervenes between the gate and the call:

1. **Re-fetch, with the same queries as Phase 1** (full bodies, no author allowlist): reviews, inline comments, issue comments, PR metadata including `state` / `headRefOid` / `statusCheckRollup`, and all three security scanners.

2. **Refuse the merge if ANY of these holds.** Each is a stop, not a warning:

   | Condition | Why it blocks |
   |---|---|
   | Any review with `id > last_review_id` from an author other than self / `synthorg-repo-bot[bot]` | Unread reviewer output. This is the one that was missed. |
   | Any inline or issue comment past its cursor (excluding self and own `@coderabbitai review` pings) | Same, on the other two streams. |
   | The reviewer's own status check is non-terminal (`CodeRabbit` context in `PENDING` / `EXPECTED` / `IN_PROGRESS` / `QUEUED`) | **A review is being written right now.** Merging here guarantees the verdict lands on a closed PR. |
   | The rolling summary carries an in-progress marker (`currently processing`, `Review in progress`, or any Phase 4 marker) | Same, reported through the summary rather than the check. |
   | `headRefOid` differs from the value convergence was computed on | Something pushed; the evidence describes a different tree. |
   | `state != "OPEN"` | Already merged or closed. |
   | Any rollup entry failing or non-terminal | CI regressed or restarted after the snapshot. |
   | Any in-scope security alert open, or any scanner recorded `scan_failed` | Phase 6b's exits are FIX or DISMISS; neither is "merge anyway". |

3. **On refusal:** record `{round, action: "merge_blocked_by_freshness", head_sha: headRefOid, trigger: <which row fired>, detail: <ids / check name>}`, do NOT call merge, and fold whatever arrived into the working set via Phase 6 (collect) so it is triaged and fixed this round. A review that arrives during the gap is ordinary new feedback and gets the ordinary treatment; the only thing the gate changes is that it is seen before the PR closes rather than after.

4. **Adjacency.** The gate's fetch and the merge call must be adjacent, with no waiting operation between them. If more than ~60 seconds elapse, or any monitor / sleep / fix cycle runs in between, the evidence is stale again and the gate re-runs from step 1. Verifying CI and then merging is not sufficient on its own: CI and the reviewer are independent clocks, and reading only the faster one is what produced the #2786 miss.

5. **An operator instruction never removes this gate.** A standing instruction such as "merge once CI is green" removes the requirement for a reviewer *verdict* (the convergence bullet above), because the operator has decided they do not need the reviewer's approval to ship. It does not license merging over reviewer output that already exists and has never been read -- the operator asked to stop waiting for an opinion, not to discard one already given. When such an instruction is in force, this gate still runs in full; only the "CodeRabbit no-findings signal" convergence bullet is waived, and the waiver is recorded on the merge history entry.

If converged AND Phase 3a passes:
- Append history `{round, action: "converged", checks_passed: N, freshness_gate: "passed", evidence_fetched_at: <ISO>}`.
- **Squash-merge immediately, but only once per head SHA.** Convergence is not a "ready for human" handoff; the user mandate is for this skill to drive the PR all the way to `MERGED`. Compare the current `headRefOid` against `state.last_merge_attempt_headRefOid` to decide which sub-flow to enter. (Phase 11 owns clearing `state.last_merge_attempt_headRefOid` when a new commit lands; Phase 3 only reads the guard.)

  **Naming convention.** Throughout Phase 3, `headRefOid` is the in-memory variable from the Phase 1 fetch and `head_sha` is the canonical history-entry field name. They carry the same value; the two names exist only to distinguish "live PR state, just fetched" from "persisted state we wrote earlier." Every history append below MUST include `head_sha: headRefOid` so the reverse-walk lookup in sub-flow A can match entries by a single, consistent identifier. Do NOT omit `head_sha` from any append, even when the action is `merged` (the success-path entry must still carry it so a future round can confirm which head merged).

  ### Sub-flow A: same-head re-check (`headRefOid == state.last_merge_attempt_headRefOid`)

  An earlier tick already attempted this exact head. Do NOT re-issue the merge call. The merge is synchronous (no `--auto` queueing -- that flag is unreliable on this repo's branch protection setup and routinely fails to fire), so any prior attempt either landed (PR is `MERGED`) or was rejected outright (recorded as `merge_blocked`). A second call against the same head would either be a no-op or surface the same rejection.

  1. Resolve the prior outcome from `state.history` by walking entries in **reverse chronological order** (most recent first) until you find one whose `head_sha == headRefOid` AND whose `action` is one of `merge_blocked` / `merged`. Capture that entry as `prior_attempt`. If no such entry exists (e.g. state file was rewritten), treat the prior attempt as `merge_blocked` with `reason: "history lookup miss"` -- the safe default since a previous attempt that lost its history record cannot be reasoned about and the user should be told.
  2. Re-fetch live state with `gh pr view N --json state,mergedAt`, then enter exactly one of these branches:

     - **`state == "MERGED"` (`mergedAt != null`):** the merge landed (most likely the merge call itself succeeded on the prior tick and the state was written correctly; if the prior history entry was `merge_blocked` and we now see `MERGED`, the user merged manually between ticks, which is also a valid terminal). Append history `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: "MERGED"}` AND `{round, action: "merged", method: "squash", head_sha: headRefOid}`. Write state. Print the `CONVERGED + SQUASH-MERGED` line. Exit (no ScheduleWakeup).
     - **`state == "OPEN"` and `prior_attempt.action == "merge_blocked"`:** the user must unblock manually before another attempt. Append history `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: "OPEN_blocked"}`. Write state. Print the `CONVERGED, merge blocked: <prior_attempt.reason>` line using the recorded reason. Exit (no ScheduleWakeup).
     - **Fallback (any other combination):** the freshly-fetched state is something the two explicit branches above did not anticipate -- e.g. `state == "CLOSED"` (PR closed without merge between ticks), `state == "OPEN"` with `prior_attempt.action == "merged"` (the head got reverted or force-pushed back), or any unexpected GraphQL state value GitHub adds in the future. Treat as blocked so the loop never silently retries. Compute `reason = "unexpected: state=<state>, prior=<prior_attempt.action or 'none'>"`. Append history `{round, action: "merge_blocked", head_sha: headRefOid, observed_state: state, reason}` AND `{round, action: "merge_already_attempted", head_sha: headRefOid, observed_state: state}`. Write state. Print the `CONVERGED, merge blocked: <reason>` single-line variant. Exit (no ScheduleWakeup -- the user must investigate before any further automated attempt).

  ### Sub-flow B: fresh attempt (`headRefOid != state.last_merge_attempt_headRefOid`)

  1. Record `state.last_merge_attempt_headRefOid = headRefOid` (the value from the Phase 1 fetch) and write state BEFORE running the merge, so a crash mid-call still leaves the guard set (which sub-flow A then handles correctly on the next tick).
  2. Run the merge **synchronously, without `--auto`**. The auto-merge flag is unreliable in this repo's branch-protection setup and routinely fails to fire even when all required checks pass. Issue the merge directly so the call either lands the merge immediately or surfaces the rejection inline; that's the only signal the loop can act on.

     `MERGE_REASON` normalises the captured stderr into a single line of plain text (ANSI escape sequences stripped, all whitespace collapsed) so the history entry and terminal output are both legible regardless of what the underlying tool printed:

     ```bash
     MERGE_STDERR="$(gh pr merge N --squash 2>&1 >/dev/null)"
     MERGE_EXIT=$?
     # Strip ANSI escape sequences (CSI, OSC, single-character SS3 etc.)
     # and collapse all whitespace runs (including embedded newlines)
     # into a single space, then trim leading/trailing whitespace.
     MERGE_REASON="$(printf '%s' "$MERGE_STDERR" \
       | sed -E 's/\x1B\[[0-9;?]*[ -\/]*[@-~]//g; s/\x1B[]PX^_].*?\x1B\\//g; s/\x1B[@-Z\\-_]//g' \
       | tr -s '[:space:]' ' ' \
       | sed -E 's/^ //; s/ $//')"
     ```

  3. Re-fetch live state with `gh pr view N --json state,mergedAt`, then enter exactly one of these branches using the captured `MERGE_REASON` / `MERGE_EXIT` plus the freshly-fetched `state`:

     - **`state == "MERGED"` (immediate success):** append history `{round, action: "merged", method: "squash", head_sha: headRefOid}`. Write state. Print the `CONVERGED + SQUASH-MERGED` line. Exit (no ScheduleWakeup -- Phase 2's terminal exit covers any future re-entry).
     - **Otherwise (`MERGE_EXIT != 0` or `state` is `OPEN` / `CLOSED` / anything else):** the merge was rejected by branch protection / CODEOWNERS / required-review policy / etc., or convergence was satisfied but the synchronous merge couldn't fire. Append history `{round, action: "merge_blocked", head_sha: headRefOid, reason: "$MERGE_REASON"}`. Write state. Print the `CONVERGED, merge blocked: $MERGE_REASON` single-line variant. Exit (no ScheduleWakeup -- the user must unblock manually). A future push that lands a new commit will clear the guard via Phase 11 and allow a fresh attempt.

## Phase 4: CodeRabbit rate-limit dance

Inspect the most recent CodeRabbit-authored item across reviews + issue comments (`author: "coderabbitai[bot]"`). Look for these markers (case-insensitive substring):

| Marker phrase | Meaning | Action |
|---|---|---|
| `currently processing` | CodeRabbit is mid-review | No-op this tick (sleep, no ping) |
| `rate limit` / `rate-limited` / `rate limited` | CodeRabbit hit OpenRouter / OpenAI rate limit | Ping + sleep |
| `i'll be back` / `back online` / `try again later` | CodeRabbit deferred review | Ping + sleep |
| `you've reached your` / `quota` | Quota exhaustion | Ping + sleep |

**Rebase-first (mandatory, runs BEFORE the refill arithmetic below).** A rate-limit marker means the ping path is closed, not that the review path is. CodeRabbit's own limit message names two continuations: an `@coderabbitai review` comment, or "push new commits to this PR". Inside the window the ping is *rejected rather than queued*, and the rejection becomes the newest marker, which resets the perceived refill window and leaves the loop busy but not progressing. A push does not go through the limiter at all: a new head SHA triggers an auto-review.

**Authorisation and blast radius.** The rewrite is confined to the PR branch this loop was invoked on, which the operator named when they started the loop; that invocation is the authorisation. The rule may never rewrite `main` or any base branch, never rewrite a branch other than `state.pr`'s head, and never use bare `--force`. If any of those three is what the situation calls for, stop and ask instead.

So on any rate-limit marker, check for an owed rebase first. Capture each command's status and stderr separately. The `if` wrappers matter: under `set -e` a bare `VAR="$(cmd)"` assignment whose command fails ends the shell before the next line can read `$?`, which is the silent exit this rule exists to prevent.

```bash
if ! FETCH_ERR="$(git fetch origin "$DEFAULT_BRANCH" 2>&1)"; then FETCH_EXIT=1; else FETCH_EXIT=0; fi
if ! COUNT_ERR="$(git rev-list --count "HEAD..origin/$DEFAULT_BRANCH" 2>&1)"; then
  COUNT_EXIT=1
else
  COUNT_EXIT=0; BEHIND="$COUNT_ERR"
fi
```

**Validate `origin`'s FETCH URL against `state.owner_repo` before running either command.** Step 2 below checks push URLs, which is the wrong half for these two and also happens too late: the fetch and the rebase both run first, and both read from `origin`'s fetch URL, which `remote.<name>.pushurl` leaves free to point at an unrelated repository. Unvalidated, `BEHIND` is then counted against a stranger's branch and the rebase replays this PR's commits onto its history, which the push-URL check cannot catch because by then the damage is in the local branch rather than the destination. Resolve it with `git remote get-url origin` (no `--push`), require it to name `state.owner_repo` in either the SSH or HTTPS form, and on a mismatch record `{round, action: "rate_limit_rebase_wrong_fetch_remote", fetch_url: <url>, expected: state.owner_repo}` and stop the loop.

`DEFAULT_BRANCH` is the Phase 1 value, not a literal `main`. Hardcoding the name reads as correct only while the repository's default happens to be called `main`, and the failure it produces after a rename is the misleading kind: every tick records `ancestry_check_failed` with git's "couldn't find remote ref" text, which sends whoever reads it hunting for a network or credential fault rather than the rename that actually disabled the path. Validate it against the same branch-name pattern step 5 applies to `headRefName` before interpolating, and quote it, since it reaches the command line the same way.

A failed `fetch` leaves the default branch's remote-tracking ref stale and a failed `rev-list` yields no count. Treating either as zero would skip an owed rebase and fall through to the ping path, which is the branch this rule exists to avoid, so a non-zero exit is recorded as `{round, action: "ancestry_check_failed", detail: <the failing command's error>}` and retried on the next tick rather than read as "nothing owed".

**Every command on this path has a defined failure action**, and each says explicitly whether the tick stops or carries the failure forward. A failed precondition, a rebase conflict and a push failure of any kind all STOP the tick: none falls through to the refill arithmetic or the ping, because those would spend a trigger while the worktree sits mid-rebase, the branch sits unpushed, or the loop has just declined to touch a branch it could not identify. A gate failure is the one exception, and it is handled as a finding rather than a stop, because by then the rebase has already rewritten local history that only a push can settle.

| Condition | Action |
|---|---|
| `FETCH_EXIT` or `COUNT_EXIT` non-zero | Record `ancestry_check_failed`, ScheduleWakeup, stop the tick. Do **not** infer a count. |
| `BEHIND` non-zero AND `baseRefName != DEFAULT_BRANCH` (a non-default base), both from the Phase 1 fetch | Do **not** rebase-push for the review: a push onto a non-default base buys no auto-review ([[coderabbit_skips_stacked_pr_auto_review]]), so it would rewrite history for nothing. Fall through to the refill arithmetic and the ping path. |
| `BEHIND` non-zero, not stacked | Run the rebase sequence below. |
| `BEHIND` zero | No rebase owed. Fall through to the refill arithmetic below. |

The non-default-base row deliberately tests the base branch and **not** whether the PR is stacked on another PR. Those are different populations: a PR from a feature branch onto a release or maintenance branch is not stacked, yet it is covered here and must be, because CodeRabbit's refusal is worded `Auto reviews are disabled on base/target branches other than the default branch` and so keys on the base alone. Narrowing this to a true stacked-PR predicate would let exactly those maintenance-branch PRs rebase-push for a review that is never coming. The row is named for the condition it tests rather than the case that motivated it, since the case is a strict subset.

Rebase sequence, in order, stopping at the first failure:

1. Check `git status --porcelain` first, and treat a non-zero exit from it the same as dirty output. Tracked staged or unstaged changes mean the tick started on a dirty worktree, which is not a rebase problem: record `{round, action: "rate_limit_rebase_blocked_dirty", detail: <status output or failure>}`, ScheduleWakeup, stop the tick, and do **not** run `git rebase` at all.

   Untracked-only output (`??` lines) is deliberately NOT treated as dirty. This repo carries untracked files as a steady state, so failing closed on them would disable the whole rebase path permanently rather than occasionally. The residual risk is narrow and already covered: a rebase touches untracked files only when a replayed commit would overwrite one, and git refuses the rebase outright in that case, which step 3 catches, aborts and records. So the untracked exception cannot produce a silently wrong result, only a rebase failure that is handled.
2. **Establish that the local branch IS the PR head, that the PR head is a branch this path is allowed to rewrite, and that this checkout may push to it.** All three checks belong here, before `git rebase`, not before the push: by push time history has already been rewritten, so a failure there strands commits that a failure here costs nothing to avoid. Capture `PRE_REBASE_SHA="$(git rev-parse HEAD)"` in the same step; every failure record from step 3 onward carries it as `pre_rebase_sha`, because it is the one value that makes the rewrite recoverable by hand. Step 2's own failures all precede `git rebase`, so the branch still points at that commit and recording it there would be noise rather than an anchor; the head-identity record below carries the value regardless, as `local_sha`, because there it is the evidence and not the recovery point.

   - **Head identity.** `PRE_REBASE_SHA` must equal the Phase 1 `headRefOid`. If it does not, the local branch and the PR head have diverged, and the direction does not matter: when local is behind, the rebase replays only the commits this checkout happens to have, while the lease still matches the remote it was told to expect, so the push silently destroys whatever landed on the PR in between. That is the one outcome on this path that loses committed work rather than merely failing. Do **not** try to reconcile it automatically; a `git pull` here would be guessing at whose commits win. Record `{round, action: "rate_limit_rebase_head_mismatch", local_sha: PRE_REBASE_SHA, pr_head_sha: headRefOid}` and **stop the loop** without a ScheduleWakeup.
   - **Branch class.** `headRefName` must be neither `DEFAULT_BRANCH` nor `baseRefName`, both from the Phase 1 fetch. This is what makes the never-rewrite-a-base-branch rule above executable rather than merely declared. Nothing else on this path enforces it: head identity passes for a same-repository PR opened *from* the default branch, and the only thing standing between such a PR and a force push today is the stacked-PR row declining to rebase whenever the base is not the default branch. That row is a rule about CodeRabbit's auto-review behaviour that happens to overlap, and it stops overlapping the moment someone revisits it on its own merits. Record `{round, action: "rate_limit_rebase_protected_branch", head_ref: headRefName, base_ref: baseRefName, default_branch: DEFAULT_BRANCH}` and stop the loop. It sits ahead of the repository check because it is a decision about whether this branch may be rewritten at all, which is settled before any work goes into resolving where to send the result. The guard is deliberately confined to this rewrite path: an ordinary Phase 10 fast-forward onto the PR head is the loop's entire job whatever that branch is called, and the prohibition above is about rewriting history, not about pushing to it.
   - **Head repository.** Compute `HEAD_REPO` from the Phase 1 `headRepositoryOwner.login` and `headRepository.name`. If it differs from `state.owner_repo` the PR is from a fork, and this checkout has no verified write path into it: record `{round, action: "rate_limit_rebase_fork_head", head_repo: HEAD_REPO}` and stop the loop. When it matches, resolve the destination by enumerating remote **names** with `git remote` and asking each one for **every** push URL it has, using `git remote get-url --push --all <name>`. Three details are load-bearing.

     Do not parse `git remote -v`: it prints a separate row per remote for fetch and for push, so a single correctly-configured remote yields two matching rows and a naive count reads it as ambiguous, stopping the loop on the ordinary case rather than the broken one. Match the **push** URL, never the fetch URL, because `remote.<name>.pushurl` can point somewhere else entirely and the push URL is where this operation lands. And pass `--all`, because a remote may carry several push URLs and `git push <remote>` sends the ref to **all** of them: a remote whose first URL names `HEAD_REPO` can therefore pass a one-URL check while the same command force-pushes to a second, unverified destination.

     So accept a remote only when it has **exactly one** push URL and that URL names `HEAD_REPO` (accept the SSH and HTTPS forms, with or without a `.git` suffix), and require exactly one such remote across the whole enumeration. On zero, several, or a remote carrying multiple push URLs, record `{round, action: "rate_limit_rebase_no_remote", head_repo: HEAD_REPO, candidates: <matching remote names>}` and stop. Carry that URL forward as `verified_push_url`, never the remote name, and push to it in step 5: the name is an indirection through config that can acquire a second destination between this check and the push, while the URL is the thing actually verified. Every later reference to the destination, here and in Phase 10, names `verified_push_url`, because "the remote" is precisely the ambiguity that lets a remote name be substituted back in at the point of use. Never fall back to a bare `git push` on this path; that would resolve the destination through `push.default` and the branch's tracking config, neither of which this loop set or verified, and the operation about to be performed is a force push.

3. `git rebase "origin/$DEFAULT_BRANCH"`. **The moment this succeeds, set `rebase_push_required = true` and carry `headRefOid`, `headRefName`, `verified_push_url` and `PRE_REBASE_SHA` forward.** The trigger is the rewrite, not any later failure: from here on the branch cannot fast-forward, so every route out of this sequence that ends in a push needs the lease, including the two that reach Phase 10 without step 4 ever failing (step 5's sweep finding new feedback, and any other path that folds this round back through Phases 6 to 10). Setting the flag only on the gate failure leaves those routes selecting the ordinary fast-forward, which a rewritten branch refuses. **On failure**, the worktree is left mid-rebase only if the rebase actually started, so run `git rebase --abort` only when rebase state exists (`git rev-parse --verify --quiet REBASE_HEAD`, or a `rebase-merge` / `rebase-apply` directory under `.git`); calling `--abort` without it fails and buries the real error. **Check the abort's own exit status.** If it succeeded, or there was no rebase state to abort, record `{round, action: "rate_limit_rebase_conflict", detail: <stderr>, pre_rebase_sha: PRE_REBASE_SHA}`. If the abort itself failed, the worktree is still mid-rebase: record `{round, action: "rate_limit_rebase_abort_failed", detail: <abort stderr>, pre_rebase_sha: PRE_REBASE_SHA}` instead. The anchor matters most in that second record, where the abort could not put the branch back and only the recorded SHA says where it was. **Either way, do NOT ScheduleWakeup: stop the loop** so a human resolves it. Never leave a rebase in progress across ticks either; the next tick would read a detached HEAD as the branch state.

   The retry decision follows one criterion: **schedule another tick only when the blocker can clear without a decision.** A conflict and an untracked-path collision are deterministic, so the next attempt reproduces them exactly and a timer only manufactures churn while looking like progress. Step 1's dirty worktree is the opposite case and keeps its wakeup, because an operator mid-edit finishes on their own and the next tick legitimately finds a clean tree.
4. Re-run any gate the newly-merged code could affect. **On failure**, the rebase has already rewritten local history and cannot be aborted, so this does NOT stop the tick: record `{round, action: "rate_limit_rebase_gate_failed", gate: <name>, detail: <output>, pre_rebase_sha: PRE_REBASE_SHA}`, then treat the failure as a finding and fix it through Phases 8 to 10 this round. Because history was rewritten, Phase 10 must push with step 5's explicit lease and destination rather than a plain `git push`, so this step has to hand it the values to do that: the hand-off state step 3 already set (`rebase_push_required` plus `headRefOid`, `headRefName`, `verified_push_url` and `PRE_REBASE_SHA`) is what Phase 10 pushes with. `PRE_REBASE_SHA` is among them because Phase 10's push is governed by step 5's failure rules, every one of which records `pre_rebase_sha`, and a hand-off that omitted it would name records Phase 10 cannot construct. Do **not** ping afterwards, and do not ScheduleWakeup here; the round continues.
5. **Run the Phase 9b sweep first, then push.** Phase 9b is mandatory before *every* push, and this one is no exception just because the round reached it without fixing anything: the rebase and its gate re-run take minutes, which is exactly the window Phase 9b exists to close, and publishing a rewritten head while a new review, a new alert or a fresh CI failure is already visible ships a known-stale view and guarantees the next tick redoes the round. Run the sweep as written, and if it turns up anything, fold it into this round through Phases 6 to 10 rather than pushing here; Phase 10 then performs the push with `rebase_push_required` set, which is the same lease and destination this step specifies. Push here only when the sweep comes back clean.

   `git push --force-with-lease=<headRefName>:<headRefOid> <verified_push_url> HEAD:refs/heads/<headRefName>` (the rebase rewrote history, which is the one sanctioned case for a force push). Two things are explicit here, for the same reason: a force push resolves nothing by default that this loop has verified.

   - The **lease**, not the bare flag. The bare form compares the remote-tracking ref, which this path never refreshed for the PR branch (the ancestry check above fetches the default branch only), so it can be stale in either direction. The expected OID is `headRefOid` as most recently fetched (the Phase 9b refresh when the push runs from Phase 10, the Phase 1 fetch on this step's own path), the remote head as GitHub reported it this tick, NOT `state.last_head_sha`: the cached value is only what the loop believes it last pushed, and it is exactly wrong in the case the lease exists to catch, where something else moved the branch. If `headRefOid` is empty, stop the tick rather than pushing without a lease.
   - The **destination**, as `verified_push_url` from step 2 plus a full refspec, never a remote name. `push.default` and the branch's tracking config are ambient state this loop neither set nor checked, and a wrong answer from either sends a force push at a branch nobody asked about; a remote name reintroduces the multi-push-URL fan-out step 2 rejected it for.

   **Validate both interpolated values before building the command, and quote every argument.** `headRefName` and `headRefOid` arrive from a GitHub API response, and a branch name is close to free-form: it may contain spaces, `$`, backticks, `;` and `--`. Interpolating one unquoted into a shell command line is a command-injection surface on the single operation in this skill that can destroy work, and it is reachable by anyone who can open a PR from a branch they named. So require `headRefOid` to match `^[0-9a-f]{40}$` and `headRefName` to match `^[A-Za-z0-9._/-]+$` with no leading `-` and no `..` segment, quote the complete lease, destination and refspec arguments, and stop the tick recording `{round, action: "rate_limit_rebase_unsafe_ref", head_ref: headRefName, pre_rebase_sha: PRE_REBASE_SHA}` if either check fails. That record carries the anchor for the same reason the push failures do, and more urgently: the rebase in step 3 already landed, so the branch is rewritten and this stop leaves it unpushed. The same validation covers the Phase 10 hand-off, which builds the same push from the same two values. An empty `headRefOid` is already a stop, and this is the same rule applied to a value that is present but malformed.

   **On any non-zero exit, stop the loop without a ScheduleWakeup** and record the failure. Handling only the lease rejection would be the more dangerous half-measure: a rejection at least leaves an obvious signal in the reviewer stream, whereas a timeout, an auth failure, a rejected signature or a remote hook refusal leaves the branch looking untouched while local history has already been rewritten, and a loop that treated those as "carry on" would schedule a tick that reads `BEHIND` zero, skip this path, and never push the rewrite at all. Distinguish the two only in what gets recorded, never in whether to stop:

   - **The lease specifically failed**, meaning the remote moved between the Phase 1 fetch and the push. The only signals that establish this are the literal `stale info` that `--force-with-lease` emits, or a re-fetch of the PR head showing an OID other than `headRefOid`. Record `{round, action: "rate_limit_rebase_push_rejected", detail: <stderr>, pre_rebase_sha: PRE_REBASE_SHA}`. Do **not** classify on `rejected` or `non-fast-forward` alone: git prints those for branch protection, a declined pre-receive hook and insufficient permissions just as readily, none of which mean the branch moved, and filing them as a lease rejection sends whoever reads the record hunting for a phantom concurrent push instead of the policy or credential that actually refused.
   - **Any other non-zero exit**, including a `rejected` with no lease signal: record `{round, action: "rate_limit_rebase_push_failed", exit_code: <code>, detail: <stderr>, pre_rebase_sha: PRE_REBASE_SHA}`.

   Never retry either with a bare `--force`; against a moved remote that discards whatever landed, and against the other failures it changes nothing that caused them. `pre_rebase_sha` is in both records because it is what makes the rewrite recoverable: `git reset --hard <pre_rebase_sha>` restores the branch to the commit it had before this tick touched it. Whether that or the rewrite is the right end state depends on what else moved, which is a human's call, and the loop stops so a human makes it.
6. On success, capture the pushed SHA (`git rev-parse HEAD`) as `pushed_sha` and append `{round, action: "rate_limit_rebase_push", head_sha: pushed_sha, rebased_onto: <main sha>}`. Do **NOT** also ping: the push already triggers the review, and a ping on top is one trigger more than needed.

   Then **continue into Phase 11 rather than exiting here**, carrying `pushed_sha`. Phase 11 is where the round is counted, the three comment cursors advance, `last_action_at` is stamped and the merge guard is cleared for the new head, and none of that is optional just because this tick reached the push down the rate-limit path instead of the ordinary one. Writing `last_head_sha` here and exiting would record the one field this step happens to know and silently skip the rest, leaving the round uncounted and the merge guard still pinned to the previous head. Let Phase 11 write `last_head_sha` from `pushed_sha` exactly as it does for a Phase 10 push, and schedule the NORMAL `cadence_seconds` there, not the refill window, since a review is now inbound.

This is not a trick played on the limiter. The branch has to be rebased before it can merge, and the `check push rebased` pre-push hook blocks a push from a branch that is behind, so the rebase is work already owed; spending it during the wait buys the review for free and triggers no more reviews than necessary.

**Refill-time parsing (only when no rebase was performed).** This covers both `BEHIND` zero and the stacked-PR row, which has a rebase owed but deliberately does not spend it here. CodeRabbit states the refill ETA in one of two wordings, and both must be matched: `Refill in <N> minutes (and <M> seconds)?` and `Next review available in: <N> minutes`. Parse against the rolling-summary comment's `created_at` (or `updated_at` if the comment was edited after the limit was hit) to compute an absolute `refill_at_iso` in UTC. Regex: `/(?:Refill in|Next review available in:?)\s*\**\s*(?:(\d+)\**\s*minutes?(?:\s*(?:and)?\s*\**(\d+)\**\s*seconds?)?|(\d+)\**\s*seconds?)/i` -- the `\**` sits on BOTH sides of every captured number, because CodeRabbit wraps the value itself (`Refill in **5** minutes`), so allowing the markers only before the digits fails to match the closing pair and drops the whole parse to the ping fallback. If parsing succeeds:

- `refill_at_iso = comment.updated_at + parsed_duration` (treat the comment timestamp as the moment the limit was reported; updated_at handles edits).
- `seconds_until_refill = refill_at_iso - now()` (clamp negatives to 0).

Decision table:

| Condition | Action |
|---|---|
| `seconds_until_refill > 0` (still inside the rate-limited window) | **Do NOT ping** -- the ping would just be eaten and CodeRabbit re-posts the same limit message. Skip the API call. Schedule wakeup for `max(60, seconds_until_refill + 60)` (60s buffer past refill). Append history `{round, action: "rate_limit_defer", refill_at: refill_at_iso, sleep_seconds: K}`. |
| `seconds_until_refill <= 0` (refill window has already passed) | Ping `@coderabbitai review` and schedule the default `cadence_seconds`. The ping should now actually trigger a review. |
| Refill regex did not match (CodeRabbit changed wording, or marker came from a non-rate-limit message like "currently processing" / "I'll be back") | Fall back to the legacy behaviour: ping immediately and schedule `cadence_seconds`. |

The "no ping during rate-limit window" rule matters because pinging inside the window does not stack -- CodeRabbit doesn't queue your `@coderabbitai review` for later, it just rejects it with another rate-limit comment, which then becomes the *new* most-recent marker on the next tick and resets the perceived refill window. The loop ends up looking busy without making progress.

**Ping action (when the decision table says ping):** post `@coderabbitai review` as an issue comment via the GitHub API:

```bash
gh api "repos/$OWNER_REPO/issues/$PR/comments" -X POST -f body='@coderabbitai review'
```

Then increment `rate_limit_pings`, append history `{round, action: "rate_limit_ping", ping_count: K, refill_at: <iso-or-null>}`, ScheduleWakeup, exit.

**Important:** when scanning issue comments later, exclude any comment authored by `synthorg-repo-bot[bot]` OR with body exactly `@coderabbitai review` so the skill doesn't trip on its own pings.

There is NO upper bound on `rate_limit_pings`. The user explicitly said 10x with 15min delay is fine. The only stop is `max_rounds`.

## Phase 5: diff cache, did anything actually change?

Compute deltas vs. cached IDs:

- `new_commits` = current `headRefOid` != `state.last_head_sha`
- `new_reviews` = at least one review has `id > state.last_review_id` whose author is not self/synthorg-repo-bot. **Apply the author filter BEFORE the id comparison** (filter, then `any` / `max` over the filtered set), never `max(review.id) > cursor AND <that review's author> is external`: a higher-id self/bot review -- e.g. your own `APPROVED` review or a `synthorg-repo-bot` review -- would otherwise mask a lower-id new external review. (Change-detection gate ONLY; when it fires, Phase 6 inspects EVERY qualifying review past the cursor, never just the max -- see the Phase 6 CodeRabbit two-review caution.)
- `new_pr_comments` = at least one PR comment has `id > state.last_pr_comment_id` whose author is not self (filter author BEFORE the id comparison, same masking guard).
- `new_issue_comments` = at least one issue comment has `id > state.last_issue_comment_id` that is not self-authored AND whose body is not `@coderabbitai review` (filter self + own pings BEFORE the id comparison, same masking guard -- otherwise your own newest `@coderabbitai review` ping masks a real new issue comment beneath it).
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
- **New review submissions:** **enumerate EVERY review with `id > last_review_id`** (excluding self/bot), not just the highest-id one. The Phase 5 `max(review.id)` test only answers "did anything new arrive"; the working set is every review past the cursor. **CodeRabbit routinely posts a `COMMENTED` review carrying outside-diff findings and then, seconds later, a separate empty `APPROVED` review on the SAME head.** So the highest-id review is the empty approval AND `reviewDecision` flips to `APPROVED` while actionable findings sit in the lower-id `COMMENTED` review. NEVER treat `reviewDecision == APPROVED`, the highest-id review, or an empty-bodied latest review as proof of "no findings" -- open each review body past the cursor. Parse each review body for embedded outside-diff-range comments (CodeRabbit puts them in `<details>` blocks at the top, same parser as `/aurelio-review-pr` Phase 4).
- **New inline comments:** every PR comment with `id > last_pr_comment_id` (excluding self).
- **New issue comments:** every issue comment with `id > last_issue_comment_id` (excluding self + `@coderabbitai review` pings).

## Phase 6b: security-alert triage (FIX or DISMISS, never leave open)

Build a separate working set from the three scanner fetches in Phase 1. Scope per scanner:

- **code-scanning** (CodeQL etc.): union of alerts visible on `ref=refs/heads/$HEAD_BRANCH` AND `ref=refs/pull/$N/head` (Phase 1 queries both -- CodeQL's PR workflow writes alerts to the pull-ref, not the branch ref). Inline review comments from `github-advanced-security[bot]` link to `/security/code-scanning/<N>` URLs; the alert-number behind that URL must show up in this fetch or it slips past the gate.
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
1. **Source** = author + comment type (e.g. `coderabbitai/inline`, `coderabbitai/review`, `human:OctoCat/issue`, `CI:test-go-1.26`).
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

## Phase 9: verification is delegated to the commit/push hooks (do NOT re-run them)

**Do not manually run `ruff` / `mypy` / `pytest` / `eslint` / `go test` here.** This repo's `.pre-commit-config.yaml` already hard-gates every push:

- **pre-commit stage** (fires on Phase 10's `git commit`): `ruff`, `ruff-format`.
- **pre-push stage** (fires on Phase 10's `git push`): `mypy` (affected modules, via `run_affected_mypy.py`), `pytest-unit` (affected modules, via `run_affected_tests.py`), `eslint-web`, `go-vet` / `go-test`, plus every convention gate (kill-switch, list-pagination, no-migration-framing, schema-drift, no-magic-numbers, boundary-typed, and the rest).

A failing hook **aborts the commit or push locally**, so nothing reaches CI or CodeRabbit and no review cycle is burned. The affected-subset hooks are also strictly faster than a manual full-suite run, so re-running the full `pytest -m unit` or whole-tree `mypy` here is pure duplicated wall-clock with no extra safety.

The `skip:` list in `.pre-commit-config.yaml` only suppresses these on **pre-commit.ci** (the cloud mirror); locally they all run. Do not mistake that skip list for "these do not run on my push".

What this phase actually does: **nothing but proceed to Phase 10.** The push there is the gate. If the commit or push is rejected by a hook, Phase 10 step 4 owns the fix-forward loop (triage the hook output, fix the real issue, new commit, re-push; never `--no-verify`, never `--amend`). If a hook failure cannot be fixed this round, surface it via `AskUserQuestion` and pause the loop. The only manual check worth running here is one a hook genuinely does NOT cover (rare, e.g. a one-off repro the reviewer explicitly asked for), and even then scope it to the single file, never the whole tree.

## Phase 9b: pre-push completeness sweep (mandatory before EVERY push)

Before staging or committing, re-run the Phase 1 fetches one more time to catch comments / reviews / alerts that landed in the **race window between Phase 1 (fetch) and Phase 9 (post-fix verification)** of this same tick. A single Phase 8 + Phase 9 cycle can take 5-15 minutes; reviewer bots typically retry on a 30-second to 2-minute cadence; human reviewers are unbounded. A comment that arrives during that race window MUST land in this push, not the next one -- otherwise the loop ships a known-stale view, the next tick re-runs the same fix cycle, and the PR thrashes.

Applies to every push. The race-window risk is identical on round 1 and round 2+; only the **baseline cursor** for "what counts as new" differs:

- **Round 1**: cursors are 0, so the Phase 1 working set IS the entire reviewer history; the sweep is checking whether anything appeared after that initial paginated read.
- **Round 2+**: cursors carry the IDs from the previous tick; the Phase 1 working set is the delta since last push; the sweep is checking whether anything appeared after that delta-fetch.

The sweep mechanic is uniform. The only thing that varies between rounds is the cursor baseline, which Phase 5's diff-cache already manages.

Steps:

1. **Re-fetch reviews / inline comments / issue comments / security alerts AND the PR metadata (including the CI rollup)** with the same queries as Phase 1. Re-fetch `state`, `headRefOid`, `headRefName` and the head repository alongside `statusCheckRollup`, in the same `gh pr view` call, and carry the refreshed values into Phase 10 rather than the Phase 1 ones. Phase 10 builds a refspec and, on the rebase hand-off, a lease out of exactly those fields, and by the time it runs they are as old as this round's whole fix cycle. **Stop the tick** if `state` is no longer `OPEN` (the PR was merged or closed while fixes were being written, so the push has no destination worth having) or if `headRefOid` no longer matches what this round started from (something else moved the branch, and a lease built from the stale value is the one that quietly destroys it). Record `{round, action: "pre_push_state_changed", observed_state: state, head_sha: headRefOid}`. Use `body` verbatim (no truncation) and no author allowlist; same hard rules as the initial fetch. The CI rollup re-fetch (`gh pr view N --json statusCheckRollup`) is non-negotiable: CI state changes asynchronously while Phase 8 / Phase 9 run, and a check that flipped to `FAILURE` after the Phase 1 snapshot MUST be folded into this round, not the next one. Without this, the loop ships a "fix" while a brand-new CI failure on the same head sits unaddressed -- the very thrash this phase exists to prevent.

2. **Diff against the working set** Phase 7 was triaged from. Compute:

   - `new_reviews_since_phase1` = reviews whose `id` is greater than the maximum review id captured at Phase 1 fetch time.
   - `new_inline_comments_since_phase1` = inline comments with id greater than the Phase 1 maximum.
   - `new_issue_comments_since_phase1` = issue comments with id greater than the Phase 1 maximum, excluding self-pings (the same exclusions Phase 5 uses).
   - `new_security_alerts_since_phase1` = open alerts (per scanner) whose `number` is not in the Phase 1 set.
   - `new_ci_failures_since_phase1` = `statusCheckRollup` entries whose `conclusion == "FAILURE"` (or `CANCELLED` / `TIMED_OUT` / `ACTION_REQUIRED`) AND whose `(name, conclusion)` tuple is not in the Phase 1 set. A check that was `IN_PROGRESS` at Phase 1 and is now `FAILURE` counts as new. A check that was `FAILURE` at Phase 1 and is still `FAILURE` does NOT recount (it was already triaged in Phase 6).
   - `flipped_ci_recoveries_since_phase1` = checks that were `FAILURE` at Phase 1 and are now `SUCCESS` / `NEUTRAL` / `SKIPPED`. These are NOT new findings, but log them in the sweep entry as `recovered: [name, ...]` so the audit trail captures the transition (useful when a fix turns out to have resolved a flake transitively).

   Self-authored items (the cached `state.self_login` from Phase 0) and items the loop posted itself (e.g. rate-limit pings) are excluded the same way Phase 5 / Phase 6 exclude them. Bot items are NOT excluded -- bots are first-class reviewers.

3. **Author roster verification.** Build a set of `(author_login, item_type)` tuples across the re-fetched data. Print this set as a one-line summary in the chat output so the operator can see which authors were considered before the push lands (e.g. `pre-push roster: [(<bot-A>, inline), (<bot-B>, review), (<human-X>, review), ...]`; do not hardcode names). If any author appears that was NOT in the Phase 1 roster, that's a signal new feedback arrived; treat it as new findings even if no specific item id grew (e.g. a reviewer dismissed and resubmitted).

4. **If anything new is in scope** (any of `new_reviews_since_phase1`, `new_inline_comments_since_phase1`, `new_issue_comments_since_phase1`, `new_security_alerts_since_phase1`, or `new_ci_failures_since_phase1` is non-empty)**:** loop back to Phase 6 (collect actionable feedback) with the additional items folded into the working set -- not Phase 7 directly, because new CI failures still need their `--log-failed` output pulled by the Phase 6 collector before triage. Then Phase 7 (triage), Phase 8 (fix), Phase 9 (verify), then re-enter Phase 9b. Do NOT advance to Phase 10 with newly-arrived feedback OR a newly-failed CI check unaddressed -- that's the exact failure mode this phase exists to prevent.

5. **If nothing new arrived:** proceed to Phase 10. Append history `{round, action: "pre_push_sweep_clean", checked_at: <ISO-now>, authors: [...], ci_recovered: [...]}` so the audit trail records that the sweep ran and any opportunistic CI recoveries.

6. **Iteration cap.** If Phase 9b loops more than 3 times in a single round (i.e. every fix attempt races a new comment or CI failure), stop and `AskUserQuestion`: "Pre-push sweep has loop-bounced 3 times on PR #N; reviewer / CI is moving faster than fixes ship. Push current batch / wait / pause loop?" The user picks. This prevents pathological live-review situations from blocking the loop indefinitely.

The sweep is read-only -- no API mutations, no commits, no pushes -- so it only consumes the API budget for the same fetch set as Phase 1 (reviews, inline comments, issue comments, both code-scanning refs, the dependency-graph compare endpoint, secret-scanning, plus one `gh pr view` for the CI rollup). Time budget on a quiet PR: under 5 seconds.

## Phase 10: commit + push

1. `git add -A`
2. Commit with message `fix: babysit round R, M findings (X coderabbit, Y ci)` plus a body listing the fixed items.
3. Push, in one of two forms, and on success capture `pushed_sha = git rev-parse HEAD` for Phase 11 either way. The commit just made is the new PR head, and the `headRefOid` Phase 1 fetched now names the commit before it.

   - **Ordinary case** (`rebase_push_required` unset, which is every round that did not rebase): `git push <verified_push_url> HEAD:refs/heads/<headRefName>`, no `-u` and no force flag, since this is a fast-forward. Resolve both interpolated values here rather than assuming Phase 4 ran, because an ordinary round never enters its rebase sequence: take `headRefName` from the Phase 9b refresh, not the Phase 1 fetch, and validate it against `^[A-Za-z0-9._/-]+$` with no leading `-` and no `..` segment, and resolve `verified_push_url` by **exactly** the procedure in Phase 4 step 2, including its `git remote get-url --push --all` enumeration and its rejection of any remote carrying more than one push URL. That last part matters as much here as under a lease: `git push <remote>` fans the ref out to every push URL the remote has, so a second unverified destination receives this branch whether the push is forced or not. On a failed validation, an ambiguous remote or a multi-URL remote, stop the tick and record it rather than falling back to a bare `git push`. Relying on the branch's tracking config would resolve the destination from ambient state on the one operation whose purpose is to move the PR, and a fast-forward aimed at the wrong branch still lands, it just lands somewhere nobody asked about.
   - **After any Phase 4 rebase** (`rebase_push_required` set, whether the round arrived here because the gate re-run failed or because step 5's sweep found new feedback): local history was rewritten before these fixes were committed, so a fast-forward is refused and the push must carry step 5's explicit lease and destination, using the `headRefOid`, `headRefName`, `verified_push_url` and `PRE_REBASE_SHA` that step 3 handed over. Every failure rule in step 5 applies unchanged here, including stopping the loop on **any** non-zero exit rather than only a lease rejection.
4. Hook failures: fix the actual issue, never `--no-verify`, never `--amend`. Create a NEW commit if needed.
5. **Flaky / intermittent pre-push failures are NOT a retry signal -- they are a root-cause signal.** If a pre-push hook (especially `pytest-unit`) fails with an intermittent / load-dependent crash (Windows xdist "Fatal Python error: Aborted", `[gwN] node down`, a test that hangs under the parallel affected-suite run but passes in isolation, a SIGPIPE / broken-pipe), you MUST diagnose and fix the root cause -- you may NOT simply re-run `git push` on the hope it passes the second time. "It passed on retry" is a workaround that ships a known-flaky suite forward; it is exactly the `feedback_root_cause_only_no_workarounds` violation. Read the full hook log first (`feedback_read_hook_log_before_any_retry`), identify the actual cause (race, resource leak, event-loop starvation, FileLock contention, a kwarg that broke a hand-written mock), and fix THAT. If the root cause is a genuinely hard, pre-existing infra problem you cannot pin from the available evidence, do NOT silently retry-past it: surface the limit to the user via `AskUserQuestion` (dedicated infra investigation vs. proceed) and let them decide. The only thing you may never do is retry the push as if the failure did not happen.

## Phase 11: update state, schedule next tick

1. Update `state.json`. The variable `headRefOid` here refers to the value fetched in Phase 1 (`gh pr view N --json headRefOid`), the same identifier Phase 3 reads. Where the two differ, `current_head = pushed_sha or headRefOid`: a tick that pushed in Phase 10 moved the head after Phase 1 read it, so `headRefOid` names the commit the round started from, not the one the branch now points at. Recording the stale value would make the next tick read this loop's own push as a new commit from elsewhere, which is the exact condition `new_commits` exists to distinguish.
   - `round += 1`
   - **If `current_head != state.last_head_sha`:** clear `state.last_merge_attempt_headRefOid = ""` so the next time Phase 3 reaches convergence on this branch, the merge guard does not block a fresh attempt against the new head. Without this reset the merge would only ever fire once per babysit lifetime, regardless of how many later commits land. Do this BEFORE updating `last_head_sha` so the comparison is against the previous tick's value.
   - `last_head_sha = current_head`
   - `last_review_id = max(review.id, last_review_id)` (same for the two comment streams)
   - `last_action_at = <ISO-now>`
   - Append history `{round, action: "fixed_and_pushed", findings: M, sources: {...}}`
2. **Max-rounds check:** if `round >= max_rounds`:
   - `AskUserQuestion`: "babysit-pr hit round R/max_rounds on PR #N. Continue / stop / raise cap?"
   - On "continue": apply the user's new cap, reschedule.
   - On "stop": write state and exit (no reschedule).
   - On "raise cap": apply the new value (Other -> integer), reschedule.
3. **Reschedule.** Decide the delay first, then make exactly ONE `ScheduleWakeup` call with it. The two steps are ordered, not alternative: a tick that scheduled the cadence and then scheduled again on discovering a refusal would fire twice.

   **3a. Check whether the push was refused.** A new head normally auto-triggers a review, but CodeRabbit answers a push made inside a rate-limit window by re-stamping the rolling summary for the new range with `Review limit reached` and an ETA, exactly as it answers a ping. That refusal lands seconds after the push, so this phase is where it is visible and Phase 4 will not run again until the next tick. Re-read the rolling summary, and treat it as a refusal only when it carries a limit marker AND names the range this push created (the `<BASE_SHA>` to `<HEAD_SHA>` block whose head token equals `current_head`); a marker naming an older range is the previous refusal, not this one.

   **3b. Select the delay.** On a refusal, parse the ETA with the Phase 4 regex and take `max(cadence_seconds, seconds_until_refill + 60)`, recording `refill_at` on the history entry. Otherwise take `cadence_seconds`. Without this the loop sleeps a cadence chosen before the ETA existed, wakes to a window that has not opened, and defers; the ping then waits for whichever later tick happens to land past refill, which in practice ran an hour past the window while every intervening tick looked busy. The Phase 4 deferral branch already schedules to refill; a refusal discovered after a push is the same fact arriving through a different door and gets the same treatment.

   **3c. Schedule, once**, passing the delay chosen in 3b:

   ```text
   ScheduleWakeup({
     delaySeconds: <the delay selected in 3b>,
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

- Convergence + squash merge succeeded:

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
- **Default cadence is 300s (5 min).** CodeRabbit usually re-reviews within 5 to 10 min of a push; a 5 min poll catches the typical case on the second tick and CI shard transitions in close to real time. Pass `15m` / `30m` explicitly when you want longer slack (e.g. a known-slow image-pull job blocking the rollup). (`feedback_push_and_review_discipline.md` §7.)
- **Default push immediately after committing.** No "ready to push?" prompt. (`feedback_push_and_review_discipline.md` §1.)

### Completeness, the only sanctioned exits

- **Fix EVERYTHING valid.** Out-of-scope, pre-existing, larger work, older non-touched code, all in scope. The user's mandate.
- **Security alerts: FIX or DISMISS, never leave open.** Phase 6b. Open alerts across rounds = workflow failure.
- **Never push incomplete work.** Before every push verify: all changes committed, lint passes, tests pass, schema drift zero (`scripts/check_schema_drift_revisions.py --backend <backend>` if touching persistence), no pending TODO from this round. (`feedback_completeness.md` §1.)
- **Never silently narrow scope.** If a fix turns out to be much bigger than the finding suggested (cascading edits across many files, schema change, migration), pause and `AskUserQuestion` with the new info before shipping a partial fix. (`feedback_completeness.md` §3.)
- **Never skip flaky tests.** If a CI failure is a flaky test, the fix is to make it deterministic (mock `time.monotonic()` / `asyncio.sleep()`, eliminate the race), NOT to mark it skip / xfail / "pre-existing flaky". Flaky-test fixes ride in this round. (`feedback_completeness.md` §5.)
- **Skipped items must be factually wrong.** Each skip is logged in round-history with the concrete disproof (file:line evidence that the finding doesn't apply).
- **Never silently dismiss a security alert.** Phase 6b dismissals always carry a `dismissed_comment` AND a round-history entry.

### External reviewer hygiene

- **CodeRabbit is the only bot reviewer.** We run CodeRabbit (`coderabbitai[bot]`) and nothing else. Still fetch reviews unfiltered and categorize by author from the response: human reviewers can show up at any time and must be handled per the table below.
- **Every push round must include a CodeRabbit review.** Never treat a round as complete, and never move toward merge, on a push CodeRabbit has not reviewed. CR re-reviews automatically on each new head; if it stays silent, re-trigger with `@coderabbitai review` and wait for the review before closing the round.
- **Stale duplicate comments are artifacts.** When CodeRabbit re-posts a finding on already-fixed code (because its index was stale at review time), verify the fix exists in the current code, post `@coderabbitai resolve` on the thread, and move on. Don't re-implement. Log as `{action: "stale_duplicate_resolved", thread_id, evidence}` in history.
- **Self-pings:** when scanning issue comments, exclude any with body exactly `@coderabbitai review` so the skill doesn't mistake its own pings for new feedback.
- **Self-comments:** when scanning reviews and inline comments, exclude `synthorg-repo-bot[bot]` and your own GitHub username (resolve via `gh api user --jq .login` once and cache in `state.self_login`).
- **Outside-diff-range comments:** CodeRabbit embeds these in `<details>` blocks at the top of the review body when the affected lines are outside the diff. Parse them as actionable inline comments. They're NOT optional. (Same parser as `/aurelio-review-pr` Phase 4.)

#### Per-reviewer auto-clear behaviour (which reviews to dismiss, which to leave alone)

GitHub keeps every prior `CHANGES_REQUESTED` review attached to a PR until either (a) the reviewer submits a new review with `APPROVED` / `COMMENTED`, or (b) someone calls the dismissal API.

| Reviewer | Re-reviews on each commit? | Auto-clears its own stale `CHANGES_REQUESTED`? | Action when previous review is now stale |
|---|---|---|---|
| **CodeRabbit** (`coderabbitai[bot]`) | Yes | Yes, by submitting a new review with no actionable items on the new head | **Never call the dismissal API.** Post replies to its inline comments (or `@coderabbitai resolve` on the thread) and let the next CR review auto-clear the prior `CHANGES_REQUESTED`. Manual dismissal is wasted work AND erases reviewer context that humans use to trace the conversation. |
| **Human reviewers** | n/a | Never auto-clears | Don't dismiss without explicit operator consent. The right path is to address the feedback in a new commit and request a re-review. |

**Default rule:** reply, don't dismiss. CodeRabbit's stale `CHANGES_REQUESTED` clears itself on its next review; a human review is dismissed only with explicit operator consent. The cost of an extra `CHANGES_REQUESTED` sitting in `reviewDecision` for a tick or two is low; the cost of dismissing a still-valid finding (or erasing a reviewer thread the next operator was about to read) is high.

**When you do dismiss** (only a human review with explicit operator consent, or a genuinely frozen stale review), call the API per-review with a `message` body that names (a) the commit SHA the review was attached to, (b) the inline findings it raised, (c) the commit(s) that resolved each one:

```bash
gh api -X PUT "repos/$OWNER_REPO/pulls/$PR/reviews/$REVIEW_ID/dismissals" \
  -f message="Stale: review on commit <SHA>; finding(s) <summary> addressed in commit(s) <SHA list>. Dismissing because the review is frozen on a superseded head."
```

Log every dismissal in the round-history entry: `{round, action: "stale_review_dismissed", reviewer, review_id, original_head_sha, addressed_in_sha}`. Dismissals are auditable; never call the API without the entry.

### Mechanics

- **Never `durable: true`** on any cron primitive. Session-only. (`feedback_no_cloud_schedule.md`.)
- **Never offer cloud `/schedule`.** Default `/loop`-style scheduling is `ScheduleWakeup`. (`feedback_no_cloud_schedule.md`.)
- **Never `--no-verify`, never `--amend`.** Hook failures get fixed in a NEW commit, never bypassed.
- **Never bare `--force`, anywhere.** The rate-limit rebase in Phase 4 is the only step that rewrites history. Exactly two pushes may therefore carry a force flag, both using the explicit lease and destination Phase 4 step 5 specifies: that step itself, and the Phase 10 push of a round the rebase handed off with `rebase_push_required` set. Every other push in this skill is a fast-forward and takes no force flag at all; if one appears to need it, history was rewritten somewhere it should not have been, and that is the thing to fix.
- **Never push when local verification (Phase 9) failed.** Hold the work, fix the failure first.
- **NEVER use `cd` in Bash commands.** Use `go -C cli`, `npm --prefix web`, absolute paths, or `bash -c "cd <dir> && <cmd>"` for tools without a `-C` equivalent. (Project bash hook enforces this.)
- **Always `uv run python -m pytest`** (not bare `pytest`) on Windows; the bare form has path issues.
- **Always `-n 8`** when running pytest locally (project hook enforces).
