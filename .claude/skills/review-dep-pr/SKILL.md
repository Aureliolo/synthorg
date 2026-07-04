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

**AskUserQuestion timeouts are never an answer.** Every phase below that calls `AskUserQuestion` is subject to this rule without exception. If a call times out with no response, the tool returns a fallback message suggesting you "proceed using your best judgment" -- do NOT. Do not treat any option (including one marked Recommended) as chosen, and do not execute `gh pr merge`, `gh pr review --approve`, `gh issue create`, `gh pr close`, a push, or any other externally visible action on the strength of that fallback. State the pending question and your reasoning in plain text, then stop and wait for the user's actual reply, or re-ask later. This applies every time a timeout fires in a session, not just the first.

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

**Pin exact versions when inspecting transitive/nested dependencies.** When assessing whether a bump would break something a nested or transitive dependency does internally (e.g. checking what version of a library a tool itself calls, or reading that tool's actual source), always resolve the ACTUAL installed version from the lockfile first -- grep the relevant `node_modules/<pkg>` entry in `package-lock.json` (or the equivalent for `uv.lock` / `go.sum`) -- then inspect that exact version. Never run `npm view <pkg>` or `npm view <pkg>@latest` without pinning a version: it silently defaults to the latest registry release, which can be materially different from what this project actually resolves and produces a wrong risk assessment (e.g. checking a package's `latest` dependencies when the lockfile has pinned a much older major).

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

1. **BREAKING**: Does the removed/renamed/changed thing appear in our config or code? If yes → must fix. If no → note but no action needed. **For override/pinned-version changes specifically** (npm `overrides`/`resolutions` or an equivalent transitive-version pin), don't assess risk purely from what the manifest declares. Check the actual lockfile entry for that nested dependency (e.g. `node_modules/<parent>/node_modules/<pkg>` in `package-lock.json`) to confirm it actually resolves to the version the override claims. A manifest/lockfile mismatch (override says vX, lockfile still resolves vY) means the change is currently inert but is latent drift: the real risk materializes the next time the lockfile is regenerated from the manifest, not at merge time. Report this distinction explicitly rather than either dismissing the risk because CI is green today, or treating it as an immediate blocker because it isn't yet.
2. **DEPRECATION**: Are we using the deprecated feature? If yes → plan migration. If no → skip.
3. **NEW FEATURE**: Could we use this? Propose an explicit verdict; don't leave it at "could" -- but do not finalize the verdict yourself. For each new capability, propose one of **ADOPT** (worth turning on; record the exact change, i.e. which config key / rule / flag, in which file), **DEFER** (worth adopting but not in this PR; becomes a follow-up item), or **SKIP** (genuinely not applicable; one-line reason) as your recommendation, then surface it to the user via `AskUserQuestion` (Phase 8's ADOPT-decision gate governs this -- there is no exception that lets an assistant-proposed SKIP bypass it). Batch sensibly: group several low-stakes proposed-SKIP items into one multiSelect or single-choice question rather than one question per item, but never let your own confidence that something is "obviously not applicable" (e.g. a feature for a platform we don't use) substitute for the user actually seeing and confirming it -- that exact shortcut is what this rule exists to prevent. Newly-introduced opt-in lint / type-check / security rules are the highest-value case and **default to proposing ADOPT**: a new rule almost always encodes a real bug class the maintainers think is worth catching, and enabling it is the entire reason to read a linter's changelog. "It's only a preview rule, or it lives in a recommended preset we don't currently inherit" is a reason to propose enabling it *deliberately*, NOT a reason to skip it without asking.
4. **IMPROVEMENT**: Does it affect a feature we use? Quantify impact if possible. If acting on it needs a change on our side (opting into a new fast-path, raising a now-safe limit, switching to a new recommended setting), treat it like a NEW FEATURE and give it an **ADOPT / DEFER / SKIP** verdict too.
5. **BUGFIX**: Were we hitting this bug? Check if we have workarounds that can now be removed.
6. **SECURITY**: Does it affect our usage? What's the severity?

**Every NEW FEATURE and IMPROVEMENT item must carry one of the three verdicts (ADOPT / DEFER / SKIP) with a one-line reason; bare "no action" is not an allowed disposition for these two categories.** ADOPT and DEFER items are not optional polish; they are a primary deliverable of reviewing a changelog. Every ADOPT item flows into Phase 6's "Opt-in improvements to adopt" list and into the Phase 7 decision so the user can choose to enable it (in this PR or as a follow-up). A scan that finds an adoptable new rule/flag and then merges the PR without ever offering to enable it has failed at its core job.

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

**Lockfiles deserve special handling.** `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `uv.lock`, `poetry.lock`, `Cargo.lock`, `go.sum`, `composer.lock`, `Gemfile.lock`, etc. are almost always touched by every dependency update in their ecosystem. Two PRs touching the same lockfile are GUARANTEED to conflict on the second merge -- even when no source files overlap. Classify lockfile-only overlaps separately from source/config overlaps; they're a "rebase needed" conflict, not a hard blocker.

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
- **Opt-in improvements to adopt**: every ADOPT item from Phase 3 (new lint / type / security rules, new config flags, new fast-paths) with the exact change spelled out, i.e. which key / rule / flag in which file (e.g. "enable `RUF076` in `[tool.ruff.lint] select` in `pyproject.toml`"). For each, say whether it's cheap enough to do in this PR or better as a follow-up. If there genuinely are none, write "no new capabilities worth adopting" explicitly; do not just omit the section, because a silent omission is indistinguishable from never having looked.
- **With merge**: config improvements / workaround removals to make in this PR before merging
- **After merge**: follow-up items, including DEFER-ed adoptions (non-blocking but valuable)
- **No action needed**: if the update is genuinely clean, say so explicitly

## Phase 7: User Decision

After presenting all PR reports, route each PR by its triage state. Only invoke AskUserQuestion when there is a real decision to make.

**Multi-PR overlap question runs FIRST when applicable.** If the batch has ≥ 2 PRs sharing source or config files (per Phase 5), invoke the overlap-strategy question described below BEFORE applying the clean-PR default. The clean-PR default only fires when (a) the batch is a single PR, OR (b) the user has picked an overlap strategy AND this PR's wave is the current one (so its merge slot is sequenced correctly relative to the other PRs in the batch). Skipping the overlap question for clean PRs would short-circuit the strategy decision and let a merge collide with a still-queued sibling PR.

**Default: clean PRs auto-approve; merging always needs an explicit go-ahead.** This repo's standing preference is "approve now, merge on explicit instruction" -- approving and merging are different actions with different authorization requirements, and conflating them (silently doing both once a PR looks clean) is the single most common way this skill has gone wrong in practice. A PR is "clean" when ALL the following hold:
- CI is fully green: every check is in `SUCCESS`, `SKIPPED`, or `NEUTRAL` state. No `FAILURE`, `CANCELLED`, `TIMED_OUT`, `IN_PROGRESS`, `QUEUED`, or `PENDING` checks. The same allowed-state list Phase 8's "Merge as-is" CI re-verification uses.
- Phase 8's ADOPT-decision gate is fully answered for every row (ADOPT, DEFER, and SKIP all need a recorded user answer; see that section -- there is no shortcut here for a PR that merely "looks" clean).
- Phase 3 cross-reference produced **zero** "must fix" / "remove workaround" items.
- The PR is not a major version bump that warranted a migration-guide review (major bumps always go through the per-PR prompt even when changelog scan is clean, because the surface area is too large for a silent default).
- For multi-PR batches: the overlap question above has been resolved AND this PR's wave is current.

For a clean PR, skip the per-PR "what should we do" AskUserQuestion (there's nothing left to decide once the ADOPT gate is answered) and post the approval-with-rationale immediately -- approving does not need a fresh go-ahead. Then, before calling `gh pr merge`:
- If the user has already told you to merge this batch earlier in the conversation (e.g. said "merge", "merge it", "merge all"), proceed directly to Phase 8's merge mechanics.
- Otherwise, ask a single batch-level confirmation ("PR(s) #X[, #Y, #Z] are clean and approved -- CI green, all ADOPT-gate items answered. Merge now?") and wait for an actual answer. A timeout on this question is not a yes (see the AskUserQuestion discipline note in Phase 0) -- report status and stop.

**Approve-with-rationale is NEVER skipped, even when merging is deferred.** Approving happens regardless of whether the merge go-ahead has arrived yet; it is not gated on the same confirmation. Every `gh pr merge` invocation in this skill MUST be preceded by an approval review whose body is the three-part Decision / Changelog digest / Follow-ups rationale defined in Phase 8's "Approve with rationale" section. There is no path through this skill that calls `gh pr merge` without first posting the rationale. If you find yourself about to invoke `gh pr merge` and have not yet posted an approval review, stop and post the approval first.

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

Carry the chosen strategy into Phase 8: it dictates merge order (which PRs go in which wave, sequential vs parallel within a wave) and rebase scheduling between waves.

**Per-PR triage (only when the PR is NOT clean per the default rule above):**

For PRs with actionable items, failing CI, or a major bump that needs explicit confirmation, ask per-PR (or batched if multiple simple PRs):

```text
"What should we do with PR #<N> (<package> <from>→<to>)?"
```

Options (pick the ≤ 4 that fit this PR's situation; AskUserQuestion shows at most four, plus an automatic "Other"):

When the review surfaced **ADOPT items** (new rules / flags / capabilities worth enabling), lead with these:
- **"Adopt and merge"** (list this first / Recommended when adoption is cheap): enable the ADOPT items from Phase 6 on the PR branch, confirm CI + lint still pass, then merge. State exactly what gets turned on. This is the default for a PR whose only actionable content is opt-in wins; it is the whole reason the adoption opportunity was surfaced.
- **"Merge now, adopt later"**: merge the bump as-is and open a follow-up for the ADOPT/DEFER items (name them). Use when the adoption is worth doing but you don't want to grow this PR's scope.
- **"Merge as-is, skip adoption"**: merge the bump and consciously decline the new capability (the decision is recorded in the approval rationale's Follow-ups line).

Otherwise (major bump with nothing to adopt, or config fixes needed):
- **"Merge as-is"**: no changes needed, changelog reviewed, ship it
- **"Improve and merge"**: apply recommended config improvements / workaround removals (describe them), then merge

Always available:
- **"Investigate first"**: something needs deeper review before deciding (specify what)
- **"Close / Skip"**: don't want this update (e.g., breaking change not worth the migration)

**If CI is failing on a PR**, replace "Merge as-is" with:
- **"Fix CI and merge"**: investigate the failure, fix it, then merge

**Multiple clean PRs: per-PR triage question is skipped; one batch-level merge confirmation replaces it; the overlap question still applies if overlaps exist.**

When multiple PRs in the batch all qualify as clean (per the default rule at the top of this phase), approve all of them immediately with rationale, then ask ONE batch-level merge confirmation (`"PRs #X, #Y, #Z are all clean and approved -- CI green, no actionable items. Merge all now?"`) unless the user already told you to merge this batch earlier in the conversation. Once you have a go-ahead (from that question or from earlier explicit instruction), run Phase 8's "Merge as-is" path on each PR in sequence: one squash merge at a time, and before each merge after the first, refresh that PR's branch against the new `main` regardless of file overlap (see Phase 8's "Between merges in a multi-PR batch" note) before its own CI re-check and merge call. The per-PR triage question is what's removed; the multi-PR overlap-strategy question above is NOT removed and still fires whenever the batch has ≥ 2 PRs sharing source or config files. The batch merge confirmation runs after that strategy decision has landed (single-PR batches skip the strategy question but still get the merge confirmation).

The previous behaviour (asking "Merge all? / review individually / skip for now") was a redundant confirmation step for cases where the skill has already proven there is nothing to decide; it's now gone.

## Phase 8: Execute Decisions

### ADOPT-decision gate (MANDATORY -- emit before any `gh pr merge`)

Before executing ANY merge path below, enumerate every NEW FEATURE / IMPROVEMENT item Phase 3 produced and emit the per-PR verdict table covering all of them (each item is exactly one row), in this exact shape:

| Item | Category | Verdict (ADOPT / DEFER / SKIP) | User answer? |
|------|----------|--------------------------------|--------------|

**Hard stop (completeness):** cross-check the rendered rows against the full Phase 3 NEW FEATURE / IMPROVEMENT item set before proceeding. If any such item is absent from the table, you MUST NOT call `gh pr merge` for that PR; a dropped row hides the very decision the gate exists to force, so omission is the loophole, not just an un-answered row. Re-emit the table with the missing item(s) added.

**Hard stop (answer): every row needs a recorded user answer, including SKIP.** If any row's "User answer?" cell is not a recorded answer from a Phase 7 `AskUserQuestion` (the user explicitly chose ADOPT, DEFER, or SKIP for that item), you MUST NOT call `gh pr merge` for that PR. Go back and ask. Writing "deferred to a follow-up", "could adopt later", "not applicable", or any similar phrasing in prose, in the approval rationale's `Follow-ups:` line, or in the Phase 6 report does NOT satisfy this gate; only a recorded user answer does. There is no exception that lets the assistant finalize SKIP unilaterally, no matter how confident it is that an item is irrelevant -- an item for a platform, tool, or feature this repo doesn't use still gets a batched, low-friction question, not a silent drop. Batch aggressively to keep this from becoming noise (grouped multiSelect questions per PR or theme are fine; one question per item is not required), but every row's disposition must trace back to an actual answer.

This gate exists because the single most common failure of this skill is finding an adoptable new rule / flag / capability -- or deciding on the assistant's own authority that something isn't worth surfacing -- and then merging the PR while quietly shelving it. The table makes that impossible: an un-answered row of any verdict sits directly above the merge step, where it cannot be rationalised away mid-flow.

Apply the merge-strategy choice from Phase 7 (when Phase 7 was asked).

**Lockfile-only batch (Phase 7 skipped).** When Phase 6 detected only lockfile-only overlaps (per the Phase 5 trigger rule, Phase 7 was skipped), there is no user-supplied strategy to apply. Use the implicit lockfile-race default: pick one PR, run the "Merge as-is" path on it (CI must already be green; if not, hold the batch until it goes green or take the PR out of scope), wait for the squash merge to land on `main`, then trigger a rebase on the lockfile-conflicting PRs that didn't win the race. Trigger that rebase per the bot conventions documented under "Wave-based parallel" below (Renovate: `<rebaseLabel>` label; Dependabot: `@dependabot rebase`); wait for CI to refresh on the rebased head, then merge the next winner. Repeat until the batch is drained. No strategy question is asked; the operator can interrupt at any point if they want a different sequencing.

**Strategy-driven path (Phase 7 ran).** Apply whichever option the user picked:
- **Wave-based parallel**: process Wave 1 PRs first by squash-merging each one immediately once its CI is green and its approval-with-rationale has been posted (within a wave, the PRs are file-disjoint so the order inside the wave does not matter); then for each subsequent wave wait for the prior wave's merges to land, trigger a rebase on the next wave's PRs, wait for CI, then merge. Rebase trigger depends on the bot. **Renovate** PRs: prefer the configured rebase label, `gh pr edit <number> --add-label <rebaseLabel>` (use the value of Renovate's `rebaseLabel` option from the repo's Renovate config; default: `rebase`), because the label trigger does not depend on the exact wording of Renovate's PR-body template. As a fragile alternative you can also tick the rebase/retry checkbox via `gh pr edit --body` rewriting `- [ ] <!-- rebase-check -->` to `- [x] <!-- rebase-check -->`, but body string-manipulation breaks silently if Renovate changes its template format. **Dependabot** PRs accept `@dependabot rebase` posted as an issue comment.
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

**Do NOT skip this step.** The approval review must land before the merge call so the PR carries the rationale at the moment of merge. Do NOT collapse the rationale into the squash commit message: the approval review is the canonical venue (squash messages get rewritten by maintainers, get truncated, and don't surface in the PR conversation thread).

### Merge as-is

**Between merges in a multi-PR batch, always refresh a PR before its own merge call, regardless of file overlap.** Merging any PR in the batch moves `main`, which can put the remaining PRs into a `BEHIND` `mergeStateStatus` even when they share zero files with what just merged -- zero overlap makes this safe, not optional to skip. Before each merge after the first one in a batch:

```bash
gh pr view <number> --json mergeable,mergeStateStatus
```

If `mergeStateStatus` is `BEHIND` (it usually will be), refresh the branch:

```bash
gh api repos/<owner>/<repo>/pulls/<number>/update-branch -X PUT
```

Then wait for CI to go green on the refreshed head (`gh pr checks <number> --watch`, capped with the Bash tool's `timeout` at 600000ms) and re-confirm the approval survived (`gh pr view <number> --json reviewDecision` should still show `APPROVED`; it will unless the repo has a "dismiss stale reviews on push" branch-protection rule, in which case re-post the approval with rationale before continuing). Only then proceed to steps 1-4 below for that PR.

1. Re-verify CI is passing right before merge (time may have passed since Phase 5, or since the branch refresh above):

   ```bash
   gh pr checks <number> --json name,state
   ```

   Inspect the JSON output. All checks should have `state: "SUCCESS"`, `"SKIPPED"`, or `"NEUTRAL"`. Do NOT use jq filters with `!=` (escaping breaks on Windows bash). If any checks are failing, inform the user and switch to the "Fix CI and merge" flow instead.
2. **Approve with rationale** (per the section above): required before merge.
3. Merge:

   ```bash
   gh pr merge <number> --squash
   ```

   Do NOT pass `--auto`. The auto-merge flag is unreliable in this repo's branch-protection setup and routinely fails to fire even when all required checks pass. Issue the merge synchronously instead: confirm CI is green in step 1 above, then call `--squash` immediately so the merge either lands or surfaces a real error. If the merge call fails (required reviews not met, branch protection blocks, etc.), surface the stderr to the user and stop.
4. Verify the merge:

   ```bash
   gh pr view <number> --json state,mergedAt --jq '{state: .state, mergedAt: .mergedAt}'
   ```

   Confirm `state == "MERGED"` and `mergedAt != null`. If not merged, inform the user and surface the prior step's stderr.

### Adopt and merge

Use the **Improve and merge** mechanics below, with the adoption itself as the committed change: on the PR branch, make the exact edits the Phase 6 "Opt-in improvements to adopt" list specified (enable the new rule in the linter config, set the new flag, switch to the new recommended setting), then run the relevant local gate before pushing so you are not relying on remote CI to discover a self-inflicted break:
- new ruff rule → `uv run ruff check . ` (and `--fix` if it has an autofix); fix or `# noqa`-justify any new findings in the same commit, never blanket-disable the rule you just enabled.
- new eslint / typescript-eslint rule → `bash -c "cd web && npm run lint"` (the dashboard lint runs `--max-warnings 0`, so a new `warn`-level rule fails CI; either fix the findings or set the rule's level deliberately with a comment).
- new type-check or security gate → the matching `uv run mypy ...` / audit command from `CLAUDE.md`.

The approval rationale (Phase 8 "Approve with rationale") must name the capability adopted and the findings it surfaced, and the `Follow-ups:` line records any adoption deferred to a later PR. If enabling the rule surfaces a large backlog of findings that can't be cleanly resolved in this PR, stop and fall back to **"Merge now, adopt later"** rather than merging a half-applied rule.

### Merge now, adopt later

Run the **Merge as-is** path to land the bump. Then file (or extend) exactly ONE follow-up issue covering ALL deferred adoptions from this review run, whether they came from a single PR or an entire batch -- never one issue per item. If this is the first DEFER item surfaced in the run, create the issue; if a later PR in the same batch adds more DEFER items, edit the existing issue's body to append a new numbered section rather than opening a second issue (do not skip the follow-up; an un-filed DEFER is just a silent SKIP):

```bash
gh issue create --repo <owner>/<repo> --title "Adopt new capabilities from <batch description> dependency bumps" \
  --body "Surfaced by dependency review of #<PR1>[, #<PR2>, ...]. Adopt the following:

## 1. <capability> from <package> <version>
File: <path>
- <exact change>

## 2. <capability> from <package> <version>
File: <path>
- <exact change>
"
```

The approval rationale's `Follow-ups:` line must reference the single combined issue number so the trail is closed, for every PR that contributed a DEFER item to it.

### Improve and merge

**Before checkout:** Verify the working tree is clean (`git status --porcelain`). If it's dirty with unrelated in-progress work, do NOT ask the user to stash or commit it just to make room for this fix -- create an isolated worktree instead so their work is never touched:

```bash
git fetch origin <pr-branch>
git worktree add <path> <pr-branch>
```

Do steps 1-4 below inside that worktree, then remove it once pushed (`git worktree remove <path> --force`). Only fall back to asking the user to stash/commit if worktree creation itself fails. If the working tree is already clean, `gh pr checkout <number>` in place is fine and no worktree is needed.

1. Check out the PR branch (`gh pr checkout <number>` if working in place, or the worktree already has it checked out).
2. Make the recommended changes (config improvements, workaround removal, etc.). Run any relevant local checks/gates before committing (type-check, lint, the specific gate the change touches) so you are not relying on remote CI alone to discover a self-inflicted break.
3. Commit with descriptive message
4. Push to the PR branch. **Note:** Some bot branches (Dependabot, Renovate) may reject pushes depending on repo permissions. If push fails:
   - Create a new branch with your changes and push it
   - Open a replacement PR targeting the original base branch, linking to the original PR in the description
   - Close the original bot PR with a comment pointing to the replacement
   - **Use the replacement PR number for all remaining steps** (CI wait, merge)
5. Wait for CI to pass using `gh pr checks <active-number> --watch` (use the Bash tool's `timeout` parameter set to 600000ms to cap the wait; if it expires, warn the user that CI may be stuck and ask how to proceed). Use the replacement PR number if step 4 created one.
6. **Approve with rationale** (per the section above): required before merge. The rationale must additionally describe the improvements applied in this PR (which recommended changes were committed, and what they were verified against).
7. Merge the active PR, subject to the same explicit go-ahead requirement as every other merge path in this skill (Phase 7's clean-PR default note applies equally here: a fix being applied does not change who authorizes the merge).

### Fix CI and merge

1. Check out the PR branch (same dirty-tree-triggers-a-worktree rule as "Improve and merge" above)
2. Investigate the CI failure
3. Fix the issue, running the relevant local check first to confirm the fix actually addresses it
4. Commit and push (same bot branch fallback applies; if push fails, open a replacement PR and use that PR number for remaining steps)
5. Wait for CI to pass using `gh pr checks <active-number> --watch` (use the Bash tool's `timeout` parameter set to 600000ms to cap the wait; if it expires, warn the user that CI may be stuck and ask how to proceed)
6. **Approve with rationale** (per the section above): required before merge. The rationale must additionally describe the CI failure root cause and the fix applied.
7. Merge the active PR when green, subject to the same explicit go-ahead requirement as every other merge path in this skill.

### Close / Skip

```bash
gh pr close <number> --comment "Skipping: <reason from user>"
```

After all merges complete, if any PRs were merged, automatically run `/post-merge-cleanup` (do NOT just remind the user; execute it). If this review's fix work happened in a separate worktree (per "Improve and merge" above) and the primary working tree is dirty with unrelated in-progress work, do not let `/post-merge-cleanup`'s `git checkout main && git pull` step touch it -- run only the safe parts (`git fetch --prune`, pruning gone local branches, including the branch behind the worktree you used) and report that the primary checkout was left alone.

---

## Rules

- **NEVER skip changelog review**: every dependency update, regardless of type (CI action, Python package, Docker image), gets a full changelog analysis between the old and new versions.
- **Be specific about what affects us**: don't just list changelog items, cross-reference each one against our actual config and code usage.
- **Major version bumps get extra scrutiny**: check for a migration guide. Always fetch it if breaking changes are ambiguous or potentially affect our usage; skip only when all breaking changes are clearly in internal APIs we don't use.
- **Offering improvements is the point, not a bonus**: reviewing a changelog exists to catch four things equally: breaking changes to handle, deprecations to migrate, workarounds to remove, AND new capabilities to adopt. A new opt-in lint / type / security rule, a new config flag, or a new fast-path is an ADOPT/DEFER/SKIP decision the user gets to make (Phase 3 verdict → Phase 6 "Opt-in improvements to adopt" → Phase 7 "Adopt and merge" option), never something the assistant decides on the user's behalf. **There is no item small or obviously-irrelevant enough to skip without asking** -- Phase 8's ADOPT-decision gate requires a recorded user answer for every row, SKIP included. If a bump introduces an adoptable improvement, the skill MUST surface it and offer to enable it; merging such a PR without ever presenting the adoption choice is a skill failure, and so is the assistant quietly deciding "not applicable" on its own authority.
- **AskUserQuestion timeouts are never consent**: see the note after Phase 0. A timed-out question gets restated and the run stops there; nothing recommended, default, or "clean" is ever executed on the strength of a timeout. This applies to every AskUserQuestion call in every phase, and to every merge-confirmation this skill asks for.
- **Approving and merging are different actions with different authorization**: approving a clean, fully-answered PR can happen immediately with no fresh confirmation. Merging always needs an explicit go-ahead -- either the user said "merge" earlier in the conversation, or a single batch-level confirmation was asked and actually answered. Silently merging because a PR "looks clean" is the same failure mode as silently skipping an adoption item: the assistant deciding something the user should decide.
- **One combined follow-up issue per review run, not one per adopted item**: when multiple DEFER items exist across a PR or a batch, they land in a single issue (create once, append sections for later items), never a fresh issue per capability.
- **Never check an unpinned "latest" version when assessing a nested/transitive dependency's real behavior**: resolve the actual version from the lockfile first. An unpinned `npm view <pkg>` silently defaults to latest and can produce a risk assessment for a version that isn't even installed.
- **Override/pinned-version bumps need a lockfile check, not just a manifest read**: confirm the lockfile's nested resolution actually matches what the override declares before assessing risk from it. A manifest/lockfile mismatch is latent drift, not zero risk and not immediate risk -- report it as what it is.
- **Refresh every PR before its own merge in a multi-PR batch, regardless of file overlap**: merging one PR moves `main` and can put the others `BEHIND` even with zero shared files. Update the branch, re-verify CI, confirm the approval survived, then merge -- every time, not just when Phase 5 flagged a conflict.
- **Prefer an isolated worktree over asking the user to stash unrelated work**: if the working tree is dirty with in-progress work unrelated to this dependency bump, create a worktree for the fix instead of interrupting that work. Only ask the user to stash/commit if worktree creation itself fails.
- **Don't merge with failing CI**: if CI fails, investigate and fix first.
- **Always approve before merge, with rationale**: every `gh pr merge` invocation in this skill MUST submit `gh pr review <number> --approve --body-file <rationale-file>` first. No exception.

  **Applies to every merge path:**
  - Phase 7 clean-PR default (`PR #<N> is clean and approved ... merge now?`).
  - Phase 8 strategy paths (`Lockfile-only batch`, `Wave-based parallel`, `Strict sequential`, `Combine into one PR`, `Defer the conflicting subset`).
  - Per-PR action sections (`Merge as-is`, `Adopt and merge`, `Merge now, adopt later`, `Improve and merge`, `Fix CI and merge`).

  **Rationale content (the three-part body from Phase 8):**
  - `Decision:` one sentence stating bump type and why merging now.
  - `Changelog digest:` 2 to 4 bullets splitting **Relevant** (items that affect us) from **Reviewed but not relevant** (items that don't).
  - `Follow-ups:` `none` if clean, otherwise the deferred items the user explicitly accepted.

  **Why mandatory:** skipping the approval (even when no AskUserQuestion was invoked because the PR was clean) leaves the PR with no audit trail of *why* it was accepted. Squash commit messages don't substitute: maintainers rewrite them, GitHub truncates them, and they don't surface in the PR conversation thread.

  **If violated:** if the skill ever reaches a `gh pr merge` call without an approval-with-rationale already posted, that is a skill bug. Stop, post the rationale, then merge.
- **Grouped updates (Renovate domain groups or Dependabot groups)**: analyze each package in the group separately, then present as one combined report.
- **Preserve existing config**: when making improvements, don't refactor unrelated config. Only touch what's relevant to the update.
- **If you can't fetch release notes** (private repo, deleted releases, etc.), say so explicitly and recommend the user check manually before merging.
- **After merging**: automatically run `/post-merge-cleanup` to sync local branches; do not just remind the user.
- **Multi-PR runs check file overlap before triage**: with ≥ 2 PRs in the batch, always compute the per-file conflict map (Phase 5) and surface it in the report. Don't propose parallel merges for PRs that overlap on source/config files; lockfile-only overlaps are acceptable but expect rebase between merges.
