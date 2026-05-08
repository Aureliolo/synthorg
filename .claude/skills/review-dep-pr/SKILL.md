---
description: "Review dependency update PRs: changelog analysis, breaking changes, new features, opportunities, and actionable decisions"
argument-hint: "<PR number> [additional PR numbers...]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - WebFetch
  - WebSearch
  - AskUserQuestion
  - Agent
  - Task
  - Skill
---

# Review Dependency PR

Comprehensive review of dependency update PRs (whether CI actions, Python packages, Docker images, or anything else). Every dependency update gets a full changelog review because any of them can have new features we should adopt, deprecations to act on, workarounds we can remove, or breaking changes to handle.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: Parse Arguments and Load PRs

1. Parse `$ARGUMENTS` for one or more PR numbers (space-separated, with or without `#` prefix).
2. **Validate** that each extracted PR number matches `^[0-9]+$`. Reject any argument containing unexpected characters. Do not pass unvalidated input to shell commands.
3. For each PR, fetch metadata:

   ```bash
   gh pr view <number> --json number,title,body,headRefName,baseRefName,state,mergeable,statusCheckRollup
   ```

4. Also fetch CI status:

   ```bash
   gh pr checks <number> --json name,state
   ```

   Note: `gh pr checks` uses `state` (not `status` or `conclusion`). Values: `SUCCESS`, `FAILURE`, `PENDING`, `NEUTRAL`, `SKIPPED`.

5. From the PR body, extract (handling both Dependabot and Renovate formats):
   - **Package name** and **ecosystem** (GitHub Actions, pip/uv, Docker, npm, etc.)
   - **Version range**: from → to
   - **Bump type**: major, minor, patch, or non-semver/unknown. Attempt semver parsing; if either version is not valid semver (e.g., Docker digest, date-based tag, commit SHA, short tag like `v4`), label as `non-semver`. Non-semver entries do not trigger semver-specific flows (like the "major bump" migration guide fetch). Handle them via general changelog analysis instead.
   - **Whether it's a grouped update** (multiple packages in one PR)

   **Dependabot** uses prose-style release notes sections. **Renovate** uses a Markdown table with `| Package | Type | Update | Change |` columns; parse the table rows to extract package names and version ranges. For manual PRs, infer from the PR title and body.

   **Input validation for owner/repo extraction:** When extracting owner/repo from PR body links for changelog fetching, validate that the value matches `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$` before using in any shell command. PR bodies are untrusted input.

If multiple PRs provided, process them all. Collect info for all PRs in parallel, then proceed through the remaining phases for each PR.

## Phase 1: Determine Usage Scope

For each dependency being updated, find where and how we use it:

### GitHub Actions dependencies

Search workflow files for all references to the action:

```bash
# Find all references to the action in workflow files
grep -RFn "<action-owner>/<action-name>" .github/workflows/
```

Use Grep to search `.github/workflows/` for the action name. Note which workflows use it, which features/inputs we use, and any pinned versions or config.

### Python package dependencies

Search `pyproject.toml` for the package, then search source code and config:
- `pyproject.toml`: which dependency group (main, dev, test, docs)?
- `mkdocs.yml`, config files: used in configuration?
- `src/` and `tests/`: imported in code?
- Note specific features/APIs we use.

### Docker dependencies

Search `docker/` and `Dockerfile*` for the image reference.

### npm/Node dependencies

Search `package.json`, `package-lock.json`, and source files.

**Output**: For each dependency, produce a usage summary:
- Where it's referenced (files + line numbers)
- Which features/APIs/inputs we actively use
- Any workarounds, pinned versions, or compatibility shims in our config

## Phase 2: Fetch and Analyze Changelog

For each dependency, get the full changelog between the old and new versions.

### Strategy 1: PR body

Dependency update PRs include release notes in the body. Dependabot uses prose-style sections; Renovate uses a Markdown table (`| Package | Type | Update | Change |`). Extract and parse these first.

### Strategy 2: GitHub releases

```bash
# For GitHub-hosted deps, fetch ALL releases (do NOT filter by version in jq -- lexicographic string comparison is broken for semver)
gh api repos/<owner>/<repo>/releases --paginate --jq '.[] | {tag: .tag_name, body: .body, published_at: .published_at}'
```

After fetching, apply semver-aware filtering in your reasoning step: parse each tag into numeric (major, minor, patch) components and select only releases within the from→to range. Do not rely on jq string comparison for version filtering: `"v2.10.0" >= "v2.9.0"` is false lexicographically but true semantically.

**Detect missing intermediate releases:** Dependency update PR bodies may truncate release notes for multi-version jumps (common with Dependabot; Renovate tables typically only show from/to). Compare the tags in the from→to range against what's already in the PR body. Fetch individual release notes for any versions NOT covered in the PR body; these may contain important changes (features, deprecations, bugfixes) that were omitted.

### Strategy 3: WebFetch

If the PR body has links to release notes or changelogs, fetch them:
- CHANGELOG.md links
- GitHub release page links
- Documentation migration guides (especially for major bumps)

### Strategy 4: WebSearch (fallback)

If release notes are incomplete, search for `"<package> <version> changelog"` or `"<package> migration guide"`.

### For major version bumps: check for a migration guide

Major bumps often have breaking changes. Check if a migration guide exists:
- Migration/upgrade guide
- Breaking changes document
- Any "what's new in vN" blog post

If all breaking changes are clearly internal API that we don't import or use (e.g., handler development API when we only configure via YAML), note this and skip the fetch. If any breaking change is ambiguous or potentially affects our usage, ALWAYS fetch and review the migration guide.

### Analysis

For each version in the range, categorize every change as:

| Category | What it means |
|----------|---------------|
| **BREAKING** | Removes/renames something we use, changes behavior we depend on |
| **DEPRECATION** | Something we use is deprecated; we should plan to migrate |
| **NEW FEATURE** | New capability we could adopt to improve our setup |
| **IMPROVEMENT** | Enhancement to something we already use (perf, reliability, etc.) |
| **BUGFIX** | Fix for something that may have affected us |
| **SECURITY** | Security fix; note severity |
| **IRRELEVANT** | Change to a feature/platform we don't use |

Only list items from the first 6 categories. Omit IRRELEVANT items entirely; don't clutter the output.

## Phase 3: Cross-Reference with Our Config

For each non-IRRELEVANT changelog item, check our actual usage:

1. **BREAKING**: Does the removed/renamed/changed thing appear in our config or code? If yes → must fix. If no → note but no action needed.
2. **DEPRECATION**: Are we using the deprecated feature? If yes → plan migration. If no → skip.
3. **NEW FEATURE**: Could we use this? Would it simplify our config, improve reliability, enable something we wanted?
4. **IMPROVEMENT**: Does it affect a feature we use? Quantify impact if possible.
5. **BUGFIX**: Were we hitting this bug? Check if we have workarounds that can now be removed.
6. **SECURITY**: Does it affect our usage? What's the severity?

## Phase 4: Build Docs Site (for docs dependencies only)

**Skip this phase** if the dependency is NOT related to documentation (Zensical, mkdocstrings, griffe, etc.).

For docs-related dependencies, actually build the docs to verify nothing breaks.

**Before checkout:** Check for uncommitted changes. If the working tree is dirty (`git status --porcelain` has output), warn the user and skip the build step rather than risk losing work.

```bash
# 1. Check for dirty working tree -- skip build (don't abort the whole skill)
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: Working tree is dirty. Skipping docs build -- please commit or stash changes first."
  # Continue to Phase 5 without docs build results
else
  # 2. Save current branch and set up cleanup trap
  original_ref="$(git symbolic-ref --quiet --short HEAD || git rev-parse HEAD)"
  trap 'git checkout "$original_ref"' EXIT

  # 3. Checkout the PR branch (gh pr checkout handles fetching automatically)
  gh pr checkout <number>

  # 4. Install deps and build
  uv sync --group docs
  uv run zensical build 2>&1

  # 5. Return to original branch (trap handles this even on failure)
  trap - EXIT
  git checkout "$original_ref"
fi
```

If the build fails, capture the errors; they're likely from breaking changes that need fixing. The trap ensures the original branch is always restored, even on failure.

## Phase 5: Cross-PR File Overlap Analysis

**Skip this phase if only one PR is being reviewed.**

Parallel merges only work safely when PRs' changed-file sets are disjoint. Compute the file map per PR and flag conflicts before triage so the user can pick a merge strategy upfront instead of discovering conflicts mid-merge.

For each PR in the batch, fetch the full changed-file list using the **paginated GraphQL form** (the default approach):

```bash
gh api graphql --paginate \
  -F owner=OWNER -F repo=REPO -F pr=NUMBER \
  -f query='query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
    repository(owner:$owner,name:$repo){
      pullRequest(number:$pr){
        files(first:100,after:$endCursor){
          pageInfo{hasNextPage,endCursor}
          nodes{path,additions,deletions}
        }
      }
    }
  }' \
  --jq '.data.repository.pullRequest.files.nodes[].path'
```

Why the paginated form is the default: `gh pr view <N> --json files` is capped at 100 files by the underlying GraphQL `first: 100` query and does not paginate ([cli/cli#5368](https://github.com/cli/cli/issues/5368), [#9916](https://github.com/cli/cli/issues/9916), [#6930](https://github.com/cli/cli/issues/6930)). On a large PR the shorthand silently truncates and the overlap map ends up incomplete, which produces wrong wave recommendations. Always reach for the paginated GraphQL above first.

**Shorthand (special-case, only after an explicit precheck).** The `gh pr view <N> --json files --jq '.files[].path'` shorthand is acceptable only when you have first confirmed the PR has fewer than 100 changed files. Run the precheck inline:

```bash
if [ "$(gh pr view <N> --json changedFiles --jq .changedFiles)" -lt 100 ]; then
  gh pr view <N> --json files --jq '.files[].path'
else
  # fall back to the paginated GraphQL form above
fi
```

Skipping the precheck is forbidden: the truncation failure is silent and the resulting overlap map looks fine even when it's missing the files most likely to conflict.

Build two views:
- **Per-PR**: file count + the file list (collapse long lists if > 20 files)
- **Per-file conflict map**: which PRs touch each path that's touched by ≥ 2 PRs

**Lockfiles deserve special handling.** `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `uv.lock`, `poetry.lock`, `Cargo.lock`, `go.sum`, `atlas.sum`, `composer.lock`, `Gemfile.lock`, etc. are almost always touched by every dependency update in their ecosystem. Two PRs touching the same lockfile are GUARANTEED to conflict on the second merge -- even when no source files overlap. Classify lockfile-only overlaps separately from source/config overlaps; they're a "rebase needed" conflict, not a hard blocker.

**Classify each pair of overlapping PRs:**

| Overlap kind | Meaning | Conflict severity | Strategy hint |
|--------------|---------|-------------------|---------------|
| **None** | Disjoint file sets | Safe | Parallel merge, any order |
| **Lockfile-only** | Only `*.lock` / `*.sum` / lockfile equivalents overlap | Trivial | First merges cleanly; the rest need a rebase. The bot will rebase on its next cycle (Renovate: apply the configured rebase label, default `rebase`, or tick the rebase/retry checkbox in the PR body; Dependabot: post `@dependabot rebase`). |
| **Config-file overlap** | Same `pyproject.toml`, `package.json`, workflow YAML, `Dockerfile`, etc. | Moderate | Sequential merge with rebase between; usually mechanical (different table rows / different action versions) |
| **Source overlap** | Same `.py` / `.ts` / `.go` / `.tsx` / etc. file | High | Investigate diffs before merging either; may need manual integration |

**Group the PRs into merge waves (iterative greedy maximal-disjoint algorithm):**

Compute waves greedily by repeatedly extracting a **maximal subset** of the remaining PRs that are mutually disjoint on source-file and config-file overlap (lockfile-only overlap inside a wave is allowed; the post-merge rebase is mechanical). "Maximal" here means *cannot-be-extended-without-introducing-conflicts*, **not** *largest-possible*. The largest-possible (maximum independent set) is NP-hard in general; the greedy algorithm below runs in polynomial time and is fine for the small batch sizes (typically 5-15 PRs) this skill handles. If a future caller needs an exact optimum, they can swap in an approximation algorithm without changing the rest of the pipeline.

1. **Wave 1** = a maximal subset of all PRs in the batch, built greedily: start with the empty set, then for each PR (e.g. iterating in order of fewest conflicting peers first) add it to the wave if it's pairwise non-overlapping on source/config with every PR already in the wave; otherwise skip it for this wave.
2. Remove the Wave 1 PRs from the candidate pool.
3. **Wave 2** = a maximal subset of the remaining PRs, built the same way.
4. Repeat until every PR has been assigned to a wave.

Within any single wave, PRs are parallel-safe to merge. Between waves, rebase the next wave's PRs after the previous wave lands and re-verify CI before merging the next wave. If at any iteration no parallel-safe subset of size ≥ 2 exists (i.e. every remaining PR conflicts with every other on source/config), the remainder becomes a sequential chain ordered by smallest-conflict-footprint first; the "Combine into one PR" strategy in Phase 7 is usually preferable in that case.

**Output**: a compact overlap matrix in the report header (Phase 6), plus the wave assignment and recommended merge ordering carried into Phase 7's strategy question.

**Trigger rule for Phase 7's strategy question.** Phase 7 is only prompted when ≥ 2 PRs in the batch share **source or config files**. Lockfile-only overlaps do **not** trigger the strategy question (they are not waves-ambiguous: the bot rebases the loser of each lockfile race on its next cycle). If all overlaps in the batch are lockfile-only, state explicitly in the report ("No source/config overlap; all PRs parallel-safe; lockfile races resolve via bot rebase") and skip the merge-strategy question in Phase 7. The Phase 6 "Overlapping files" count must use the same scope: count only source/config overlaps when deciding whether to invoke Phase 7.

## Phase 6: Present Findings

When multiple PRs are in the batch, lead the report with the **Cross-PR Overlap Matrix** from Phase 5:

```text
## Batch Overlap Summary
**PRs reviewed**: #X, #Y, #Z
**Overlapping files (for strategy decision; excludes lockfile-only)**: <count>, classified as <config / source>
**Lockfile-only overlaps**: <count> (informational; do not trigger Phase 7)
**Recommended waves**: Wave 1 = #X, #Z (parallel-safe); Wave 2 = #Y (rebase after Wave 1)
```

Then for each PR, present a structured report:

### Header

```text
## PR #<number>: <title>
**Package(s)**: <name or comma-separated names> | **Ecosystem**: <type> | **Bump**: <from> → <to> (<major/minor/patch/non-semver>)
**CI Status**: <pass/fail summary>
**Files touched**: <count> (<lockfile-only | includes config | includes source>) -- conflicts with: #<other PRs that share source or config files, or "none"> (lockfile-only overlaps with other PRs are listed separately as informational, e.g. `lockfile-only: #X, #Y`; they do not count as "conflicts")
**Usage**: <brief -- e.g., "3 workflows, inputs: python-version, cache" or "mkdocs.yml theme + 2 plugins">
```

### Changelog Highlights

Present ONLY actionable items (skip IRRELEVANT):

| # | Version | Category | Change | Affects Us? | Action |
|---|---------|----------|--------|-------------|--------|
| 1 | v7.2.0 | NEW FEATURE | Added `cache-dependency-path` input | Could use: we currently don't cache | Consider adding to CI |
| 2 | v7.0.0 | BREAKING | Dropped Node 16 support | No: we don't control runner Node | None needed |
| ... | ... | ... | ... | ... | ... |

### Recommendations

List concrete actions to take, grouped by timing:
- **Before merge**: things that must be fixed for the PR to work
- **With merge**: config improvements to make in this PR before merging
- **After merge**: follow-up items (non-blocking but valuable)
- **No action needed**: if the update is clean, say so explicitly

## Phase 7: User Decision

After presenting all PR reports, route each PR by its triage state. Only invoke AskUserQuestion when there is a real decision to make.

**Default: clean PRs auto-merge without prompting.** A PR is "clean" when ALL of the following hold:
- CI is fully green (no `FAILURE` / `CANCELLED` / `TIMED_OUT` checks; pending is allowed only when the remaining checks are non-blocking advisory checks the user has previously accepted as non-gating).
- Phase 3 cross-reference produced **zero** "must fix" / "should adopt" / "remove workaround" items (i.e. no entries under the Recommendations report's "Before merge" / "With merge" sections).
- The PR is not a major version bump that warranted a migration-guide review (major bumps always go through the per-PR prompt even when changelog scan is clean, because the surface area is too large for a silent default).

For a clean PR, skip the per-PR AskUserQuestion entirely. Announce the action in plain text (`"PR #<N> is clean (CI green, no actionable items); merging as-is."`), then run Phase 8's "Merge as-is" path. The user can still interrupt before the merge is finalised; the skill's job is to not ask redundant questions.

For a PR that is NOT clean (CI failing, actionable items present, or major bump), proceed to the prompts below.

**Multi-PR overlap question (only when ≥ 2 PRs share source or config files, per Phase 5):**

Before per-PR triage, ask one batch-level question to lock in a merge strategy. Skip this question when the only overlaps in the batch are lockfile-only; those resolve automatically when the bot rebases the loser of each lockfile race on its next cycle.

```text
"<N> PRs in this batch overlap on <M> source/config file(s) (<config|source>). How should we sequence the merges?"
```

Options:
- **"Wave-based parallel"**: Merge Wave 1 PRs in parallel; rebase + merge Wave 2; repeat. Maximises throughput; lockfile-only conflicts auto-resolve when the bot rebases (Renovate on its next cycle, Dependabot on `@dependabot rebase`). Use when overlaps are lockfile/config only.
- **"Strict sequential"**: Merge one PR at a time, rebase the rest between merges. Slowest but lowest risk. Use when source files overlap or any PR's diff would non-trivially conflict.
- **"Combine into one PR"**: Close the bot PRs and create one combined PR with all changes manually integrated. Use when 3+ PRs all conflict on the same source file -- avoids N rounds of rebase churn.
- **"Defer the conflicting subset"**: Merge the disjoint PRs now; close the conflicting ones via Phase 8's "Close / Skip" flow with a recognisable deferment reason (the user provides the exact phrase when prompted; common choices: "supersede later", "deferred to next cycle", "blocked on #N"). The dependency bot (Renovate or Dependabot) will recreate the closed PRs on its next cycle once the main batch has landed. Use when the conflicting subset isn't time-sensitive. The exact comment text is whatever the user supplies; this skill does not require a specific phrase, but the chosen reason should make the intent legible to a future reader.

Carry the chosen strategy into Phase 8: it dictates merge order and whether Phase 8 invokes `--auto` (parallel-safe) vs blocking on each merge (sequential).

**Per-PR triage (only when the PR is NOT clean per the default rule above):**

For PRs with actionable items, failing CI, or a major bump that needs explicit confirmation, ask per-PR (or batched if multiple simple PRs):

```text
"What should we do with PR #<N> (<package> <from>→<to>)?"
```

Options:
- **"Merge as-is"**: No changes needed, changelog reviewed, ship it
- **"Improve and merge"**: Apply the recommended config improvements, then merge (describe what will be changed)
- **"Investigate first"**: Something needs deeper review before deciding (specify what)
- **"Close / Skip"**: Don't want this update (e.g., breaking change not worth the migration)

**If CI is failing on a PR**, replace "Merge as-is" with:
- **"Fix CI and merge"**: Investigate the failure, fix it, then merge

**Multiple clean PRs: no batch question, just announce + merge.**

When multiple PRs in the batch all qualify as clean (per the default rule at the top of this phase), the skill auto-merges them as a group. Announce in plain text (`"PRs #X, #Y, #Z are all clean (CI green, no actionable items); merging all as-is."`), then run Phase 8's "Merge as-is" path on each in sequence (or `--auto` in parallel for disjoint file sets per Phase 5). No AskUserQuestion is invoked.

The previous behaviour (asking "Merge all? / review individually / skip for now") was a redundant confirmation step for cases where the skill has already proven there is nothing to decide; it's now gone.

## Phase 8: Execute Decisions

Apply the merge-strategy choice from Phase 7 (when Phase 7 was asked).

**Lockfile-only batch (Phase 7 skipped).** When Phase 6 detected only lockfile-only overlaps (per the Phase 5 trigger rule, Phase 7 was skipped), there is no user-supplied strategy to apply. Use the implicit lockfile-race default: treat all PRs in the batch as parallel-safe; merge each eligible PR immediately (or queue with `--auto` if a final required check is still pending); after each merge lands on `main`, the lockfile-conflicting PRs that didn't win that race need a rebase. Trigger that rebase per the bot conventions documented under "Wave-based parallel" below (Renovate: `<rebaseLabel>` label; Dependabot: `@dependabot rebase`); wait for CI to refresh on the rebased head, then merge the next winner. Repeat until the batch is drained. No strategy question is asked; the operator can interrupt at any point if they want a different sequencing.

**Strategy-driven path (Phase 7 ran).** Apply whichever option the user picked:
- **Wave-based parallel**: process Wave 1 PRs first, all with `--auto`/immediate as appropriate, then for each subsequent wave wait for prior merges to land, trigger a rebase on the next wave's PRs, wait for CI, then merge. Rebase trigger depends on the bot. **Renovate** PRs: prefer the configured rebase label, `gh pr edit <number> --add-label <rebaseLabel>` (use the value of Renovate's `rebaseLabel` option from the repo's Renovate config; default: `rebase`), because the label trigger does not depend on the exact wording of Renovate's PR-body template. As a fragile alternative you can also tick the rebase/retry checkbox via `gh pr edit --body` rewriting `- [ ] <!-- rebase-check -->` to `- [x] <!-- rebase-check -->`, but body string-manipulation breaks silently if Renovate changes its template format. **Dependabot** PRs accept `@dependabot rebase` posted as an issue comment.
- **Strict sequential**: merge one PR, wait for it to land on `main`, trigger rebase + CI on the next PR, then merge it. No overlap with other merges in flight.
- **Combine into one PR**: invoke "Improve and merge" against a single new branch that integrates all the diffs; close the bot PRs with a pointer to the combined PR.
- **Defer the conflicting subset**: invoke "Close / Skip" on the deferred PRs first, then process the remaining disjoint PRs normally.

For each PR based on user's choice:

### Approve with rationale (MANDATORY before any merge)

**Every merge path in this skill funnels through this step first**, including all five strategy paths in Phase 8 (`Lockfile-only batch`, `Wave-based parallel`, `Strict sequential`, `Combine into one PR`, `Defer the conflicting subset`) and all three per-PR action sections below (`Merge as-is`, `Improve and merge`, `Fix CI and merge`). Before invoking `gh pr merge` for any PR, post a PR approval whose body is a **three-part structured rationale** (a one-sentence Decision, a Changelog digest paragraph with 2 to 4 explicit bullets, and a Follow-ups line). This leaves a durable artifact on the PR (visible to future reviewers, audit trails, and bisects) explaining what was scanned and what was deemed relevant; without it, "merged by Renovate label, no comment" becomes the only signal in the timeline.

The `<rationale>` body is multi-line by design. `gh pr review --body` accepts newlines (the bash `"..."` quoting preserves them), but for any rationale beyond a single sentence, prefer one of the multi-line forms below over inlining a long quoted string: heredoc piped to `--body-file -`, or write to a temp file first and pass `--body-file <path>`. Inline `"..."` quoting is fine only for the rare patch-bump-with-nothing-relevant case where the entire rationale fits on one line.

The body MUST contain three sections in this order, each prefixed with its literal label so reviewers across PRs produce a consistent format:

1. **`Decision:`** one sentence describing the bump type (patch / minor / major / lockfile / digest) and why it's being merged (`CI green`, `no breaking changes affecting us`, `migration applied in this PR`, etc.).
2. **`Changelog digest:`** a short paragraph followed by 2 to 4 markdown bullets (use `-` on a fresh line, indented two spaces if nested under a sub-label) summarising the Phase 2 scan:
   - which versions were covered (from -> to)
   - **Relevant items** that affect us (new features adopted / deprecations actioned / bug fixes we were hitting / security fixes that matter)
   - **Reviewed but not relevant** items (breaking changes in features we don't use, irrelevant platform changes, removed APIs we never imported)
3. **`Follow-ups:`** one line; `none` if clean, otherwise the deferred items the user explicitly accepted in Phase 7 (e.g. "adopt new `--cache-dependency-path` input in a follow-up PR").

Recommended invocation patterns (pick whichever matches the rationale length):

```bash
# Multi-line rationale via heredoc piped through stdin (preferred for typical 3-section bodies)
gh pr review <number> --approve --body-file - <<'EOF'
Decision: Patch bump 0.11.7 -> 0.11.8; CI green; no breaking changes touching our usage.

Changelog digest:
- Covered 0.11.7 -> 0.11.8 (single release).
- Relevant: bug fix for `uv lock` on `pyproject.toml` files containing only dependency-groups (we hit this on the docs-toolchain refactor).
- Reviewed but not relevant: new `--python-downloads-json-url` flag (we don't customise download sources); `UV_NO_PROJECT` env var (no use case yet).

Follow-ups: none.
EOF

# Long rationale via temp file (when heredoc gets unwieldy or you want to review the body before posting)
gh pr review <number> --approve --body-file /tmp/dep-approval-<number>.txt

# One-liner only when the rationale truly fits on one line
gh pr review <number> --approve --body "Decision: lockfile-only refresh; CI green; no source diffs. Changelog digest: not applicable for lockFileMaintenance. Follow-ups: none."
```

**Do NOT skip this step**, even when `--auto` is used and the merge happens asynchronously: the approval must land first so the PR carries the rationale before it auto-merges. Do NOT collapse the rationale into the squash commit message; the approval review is the canonical venue (squash messages get rewritten by maintainers, get truncated, and don't surface in the PR conversation thread).

### Merge as-is

1. Re-verify CI is passing right before merge (time may have passed since Phase 5):

   ```bash
   gh pr checks <number> --json name,state
   ```

   Inspect the JSON output. All checks should have `state: "SUCCESS"`, `"SKIPPED"`, or `"NEUTRAL"`. Do NOT use jq filters with `!=` (escaping breaks on Windows bash). If any checks are failing, inform the user and switch to the "Fix CI and merge" flow instead.
2. **Approve with rationale** (per the section above): required before merge.
3. Merge:

   ```bash
   gh pr merge <number> --squash --auto
   ```

   Note: `--auto` may succeed silently with no stdout. Track which path was used: `auto` or `immediate`.

   If `--auto` fails (auto-merge not enabled on the repo or branch protection requirements not met), fall back to `gh pr merge <number> --squash` for immediate merge. If that also fails (e.g., required reviews not met), inform the user that manual approval is needed.
4. Verify the merge:

   ```bash
   gh pr view <number> --json state,autoMergeRequest --jq '{state: .state, autoMerge: .autoMergeRequest}'
   ```

   - If **immediate** merge was used: confirm `state` is `MERGED`. If not, inform the user.
   - If **auto** merge was enabled: `state` will be `OPEN` with `autoMergeRequest` present (auto-merge is asynchronous; it fires after required checks pass). Inform the user: "Auto-merge has been enabled; the PR will merge automatically when all required checks pass." No immediate state verification needed.

### Improve and merge

**Before checkout:** Verify the working tree is clean (`git status --porcelain`). If dirty, warn the user and ask them to commit or stash first.

1. Check out the PR branch using `gh pr checkout <number>`
2. Make the recommended changes (config improvements, workaround removal, etc.)
3. Commit with descriptive message
4. Push to the PR branch. **Note:** Some bot branches (Dependabot, Renovate) may reject pushes depending on repo permissions. If push fails:
   - Create a new branch with your changes and push it
   - Open a replacement PR targeting the original base branch, linking to the original PR in the description
   - Close the original bot PR with a comment pointing to the replacement
   - **Use the replacement PR number for all remaining steps** (CI wait, merge)
5. Wait for CI to pass using `gh pr checks <active-number> --watch` (use the Bash tool's `timeout` parameter set to 600000ms to cap the wait; if it expires, warn the user that CI may be stuck and ask how to proceed). Use the replacement PR number if step 4 created one.
6. **Approve with rationale** (per the section above): required before merge. The rationale must additionally describe the improvements applied in this PR (which recommended changes were committed).
7. Merge the active PR.

### Fix CI and merge

1. Check out the PR branch using `gh pr checkout <number>` (same dirty-tree check as above)
2. Investigate the CI failure
3. Fix the issue
4. Commit and push (same bot branch fallback applies; if push fails, open a replacement PR and use that PR number for remaining steps)
5. Wait for CI to pass using `gh pr checks <active-number> --watch` (use the Bash tool's `timeout` parameter set to 600000ms to cap the wait; if it expires, warn the user that CI may be stuck and ask how to proceed)
6. **Approve with rationale** (per the section above): required before merge. The rationale must additionally describe the CI failure root cause and the fix applied.
7. Merge the active PR when green.

### Close / Skip

```bash
gh pr close <number> --comment "Skipping: <reason from user>"
```

After all merges complete, if any PRs were merged, automatically run `/post-merge-cleanup` (do NOT just remind the user; execute it).

---

## Rules

- **NEVER skip changelog review**: every dependency update, regardless of type (CI action, Python package, Docker image), gets a full changelog analysis between the old and new versions.
- **Be specific about what affects us**: don't just list changelog items, cross-reference each one against our actual config and code usage.
- **Major version bumps get extra scrutiny**: check for a migration guide. Always fetch it if breaking changes are ambiguous or potentially affect our usage; skip only when all breaking changes are clearly in internal APIs we don't use.
- **Don't merge with failing CI**: if CI fails, investigate and fix first.
- **Always approve before merge, with rationale**: every `gh pr merge` invocation in this skill (whether reached via a Phase 8 strategy path - `Lockfile-only batch`, `Wave-based parallel`, `Strict sequential`, `Combine into one PR`, `Defer the conflicting subset` - or via a per-PR action section - `Merge as-is`, `Improve and merge`, `Fix CI and merge`) MUST submit `gh pr review <number> --approve --body "<rationale>"` first. The rationale records the decision (bump type + why merging), a 2 to 4 bullet changelog digest splitting **Relevant** vs **Reviewed but not relevant**, and any deferred follow-ups. Skipping the approval (even when `--auto` will land the PR asynchronously) leaves the PR with no audit trail of *why* it was accepted; squash commit messages don't substitute because they get rewritten by maintainers and don't surface in the PR conversation thread.
- **Grouped updates (Renovate domain groups or Dependabot groups)**: analyze each package in the group separately, then present as one combined report.
- **Preserve existing config**: when making improvements, don't refactor unrelated config. Only touch what's relevant to the update.
- **If you can't fetch release notes** (private repo, deleted releases, etc.), say so explicitly and recommend the user check manually before merging.
- **After merging**: automatically run `/post-merge-cleanup` to sync local branches; do not just remind the user.
- **Multi-PR runs check file overlap before triage**: with ≥ 2 PRs in the batch, always compute the per-file conflict map (Phase 5) and surface it in the report. Don't propose parallel merges for PRs that overlap on source/config files; lockfile-only overlaps are acceptable but expect rebase between merges.
