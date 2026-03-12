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

Comprehensive review of dependency update PRs — whether CI actions, Python packages, Docker images, or anything else. Every dependency update gets a full changelog review because any of them can have new features we should adopt, deprecations to act on, workarounds we can remove, or breaking changes to handle.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: Parse Arguments and Load PRs

1. Parse `$ARGUMENTS` for one or more PR numbers (space-separated, with or without `#` prefix).
2. **Validate** that each extracted PR number matches `^[0-9]+$`. Reject any argument containing unexpected characters — do not pass unvalidated input to shell commands.
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
   - **Bump type**: major, minor, patch, or non-semver/unknown. Attempt semver parsing; if either version is not valid semver (e.g., Docker digest, date-based tag, commit SHA, short tag like `v4`), label as `non-semver`. Non-semver entries do not trigger semver-specific flows (like the "major bump" migration guide fetch) — handle them via general changelog analysis instead.
   - **Whether it's a grouped update** (multiple packages in one PR)

   **Dependabot** uses prose-style release notes sections. **Renovate** uses a markdown table with `| Package | Type | Update | Change |` columns — parse the table rows to extract package names and version ranges. For manual PRs, infer from the PR title and body.

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
- `pyproject.toml` — which dependency group (main, dev, test, docs)?
- `mkdocs.yml`, config files — used in configuration?
- `src/` and `tests/` — imported in code?
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

Dependabot PRs include release notes in the body. Extract and parse these first.

### Strategy 2: GitHub releases

```bash
# For GitHub-hosted deps, fetch ALL releases (do NOT filter by version in jq — lexicographic string comparison is broken for semver)
gh api repos/<owner>/<repo>/releases --paginate --jq '.[] | {tag: .tag_name, body: .body, published_at: .published_at}'
```

After fetching, apply semver-aware filtering in your reasoning step: parse each tag into numeric (major, minor, patch) components and select only releases within the from→to range. Do not rely on jq string comparison for version filtering — `"v2.10.0" >= "v2.9.0"` is false lexicographically but true semantically.

**Detect missing intermediate releases:** Dependabot PR bodies often truncate release notes for multi-version jumps. Compare the tags in the from→to range against what's already in the PR body. Fetch individual release notes for any versions NOT covered in the PR body — these may contain important changes (features, deprecations, bugfixes) that were omitted from the truncated view.

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
| **DEPRECATION** | Something we use is deprecated — we should plan to migrate |
| **NEW FEATURE** | New capability we could adopt to improve our setup |
| **IMPROVEMENT** | Enhancement to something we already use (perf, reliability, etc.) |
| **BUGFIX** | Fix for something that may have affected us |
| **SECURITY** | Security fix — note severity |
| **IRRELEVANT** | Change to a feature/platform we don't use |

Only list items from the first 6 categories. Omit IRRELEVANT items entirely — don't clutter the output.

## Phase 3: Cross-Reference with Our Config

For each non-IRRELEVANT changelog item, check our actual usage:

1. **BREAKING**: Does the removed/renamed/changed thing appear in our config or code? If yes → must fix. If no → note but no action needed.
2. **DEPRECATION**: Are we using the deprecated feature? If yes → plan migration. If no → skip.
3. **NEW FEATURE**: Could we use this? Would it simplify our config, improve reliability, enable something we wanted?
4. **IMPROVEMENT**: Does it affect a feature we use? Quantify impact if possible.
5. **BUGFIX**: Were we hitting this bug? Check if we have workarounds that can now be removed.
6. **SECURITY**: Does it affect our usage? What's the severity?

## Phase 4: Build Docs Site (for docs dependencies only)

**Skip this phase** if the dependency is NOT related to documentation (MkDocs, mkdocstrings, griffe, etc.).

For docs-related dependencies, actually build the docs to verify nothing breaks.

**Before checkout:** Check for uncommitted changes. If the working tree is dirty (`git status --porcelain` has output), warn the user and skip the build step rather than risk losing work.

```bash
# 1. Check for dirty working tree — skip build (don't abort the whole skill)
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: Working tree is dirty. Skipping docs build — please commit or stash changes first."
  # Continue to Phase 5 without docs build results
else
  # 2. Save current branch and set up cleanup trap
  original_ref="$(git symbolic-ref --quiet --short HEAD || git rev-parse HEAD)"
  trap 'git checkout "$original_ref"' EXIT

  # 3. Checkout the PR branch (gh pr checkout handles fetching automatically)
  gh pr checkout <number>

  # 4. Install deps and build
  uv sync --group docs
  uv run mkdocs build --strict 2>&1

  # 5. Return to original branch (trap handles this even on failure)
  trap - EXIT
  git checkout "$original_ref"
fi
```

If the build fails, capture the errors — they're likely from breaking changes that need fixing. The trap ensures the original branch is always restored, even on failure.

## Phase 5: Present Findings

For each PR, present a structured report:

### Header

```text
## PR #<number>: <title>
**Package(s)**: <name or comma-separated names> | **Ecosystem**: <type> | **Bump**: <from> → <to> (<major/minor/patch/non-semver>)
**CI Status**: <pass/fail summary>
**Usage**: <brief — e.g., "3 workflows, inputs: python-version, cache" or "mkdocs.yml theme + 2 plugins">
```

### Changelog Highlights

Present ONLY actionable items (skip IRRELEVANT):

| # | Version | Category | Change | Affects Us? | Action |
|---|---------|----------|--------|-------------|--------|
| 1 | v7.2.0 | NEW FEATURE | Added `cache-dependency-path` input | Could use — we currently don't cache | Consider adding to CI |
| 2 | v7.0.0 | BREAKING | Dropped Node 16 support | No — we don't control runner Node | None needed |
| ... | ... | ... | ... | ... | ... |

### Recommendations

List concrete actions to take, grouped by timing:
- **Before merge**: things that must be fixed for the PR to work
- **With merge**: config improvements to make in this PR before merging
- **After merge**: follow-up items (non-blocking but valuable)
- **No action needed**: if the update is clean, say so explicitly

## Phase 6: User Decision

After presenting all PR reports, use AskUserQuestion to ask how to proceed. Tailor options based on what was found.

**If there are actionable items (config improvements, new features to adopt, workarounds to remove):**

Ask per-PR (or batched if multiple simple PRs):

```text
"What should we do with PR #<N> (<package> <from>→<to>)?"
```

Options:
- **"Merge as-is"** — No changes needed, changelog reviewed, ship it
- **"Improve and merge"** — Apply the recommended config improvements, then merge (describe what will be changed)
- **"Investigate first"** — Something needs deeper review before deciding (specify what)
- **"Close / Skip"** — Don't want this update (e.g., breaking change not worth the migration)

**If CI is failing on a PR**, replace "Merge as-is" with:
- **"Fix CI and merge"** — Investigate the failure, fix it, then merge

**If multiple PRs are all clean (no actionable items AND CI is passing):**

A PR is only eligible for batch merging when it has both no actionable changelog items AND all CI checks are passing. PRs with failing CI must always be routed to the per-PR flow, regardless of changelog cleanliness.

Batch them into one question:

```text
"PRs #X, #Y, #Z all look clean after changelog review (CI passing). Merge all?"
```

Options:
- **"Merge all"** — Ship them all
- **"Let me review individually"** — Break out per-PR decisions
- **"Skip for now"** — Come back later

## Phase 7: Execute Decisions

For each PR based on user's choice:

### Merge as-is

1. Re-verify CI is passing right before merge (time may have passed since Phase 5):

   ```bash
   gh pr checks <number> --json name,state
   ```

   Inspect the JSON output — all checks should have `state: "SUCCESS"`, `"SKIPPED"`, or `"NEUTRAL"`. Do NOT use jq filters with `!=` (escaping breaks on Windows bash). If any checks are failing, inform the user and switch to the "Fix CI and merge" flow instead.
2. Merge:

   ```bash
   gh pr merge <number> --squash --auto
   ```

   Note: `--auto` may succeed silently with no stdout. Track which path was used: `auto` or `immediate`.

   If `--auto` fails (auto-merge not enabled on the repo or branch protection requirements not met), fall back to `gh pr merge <number> --squash` for immediate merge. If that also fails (e.g., required reviews not met), inform the user that manual approval is needed.
3. Verify the merge:

   ```bash
   gh pr view <number> --json state,autoMergeRequest --jq '{state: .state, autoMerge: .autoMergeRequest}'
   ```

   - If **immediate** merge was used: confirm `state` is `MERGED`. If not, inform the user.
   - If **auto** merge was enabled: `state` will be `OPEN` with `autoMergeRequest` present (auto-merge is asynchronous — it fires after required checks pass). Inform the user: "Auto-merge has been enabled; the PR will merge automatically when all required checks pass." No immediate state verification needed.

### Improve and merge

**Before checkout:** Verify the working tree is clean (`git status --porcelain`). If dirty, warn the user and ask them to commit or stash first.

1. Check out the PR branch using `gh pr checkout <number>`
2. Make the recommended changes (config improvements, workaround removal, etc.)
3. Commit with descriptive message
4. Push to the PR branch. **Note:** Dependabot branches may reject pushes depending on repo permissions. If push fails:
   - Create a new branch with your changes and push it
   - Open a replacement PR targeting the original base branch, linking to the original PR in the description
   - Close the original Dependabot PR with a comment pointing to the replacement
   - **Use the replacement PR number for all remaining steps** (CI wait, merge)
5. Wait for CI to pass using `gh pr checks <active-number> --watch --timeout 600` (10-minute timeout — if it expires, warn the user that CI may be stuck and ask how to proceed rather than hanging indefinitely). Use the replacement PR number if step 4 created one.
6. Merge the active PR

### Fix CI and merge

1. Check out the PR branch using `gh pr checkout <number>` (same dirty-tree check as above)
2. Investigate the CI failure
3. Fix the issue
4. Commit and push (same Dependabot fallback applies — if push fails, open a replacement PR and use that PR number for remaining steps)
5. Wait for CI to pass using `gh pr checks <active-number> --watch --timeout 600` (10-minute timeout — if it expires, warn the user that CI may be stuck and ask how to proceed rather than hanging indefinitely)
6. Merge the active PR when green

### Close / Skip

```bash
gh pr close <number> --comment "Skipping: <reason from user>"
```

After all merges complete, if any PRs were merged, automatically run `/post-merge-cleanup` (do NOT just remind the user — execute it).

---

## Rules

- **NEVER skip changelog review** — every dependency update, regardless of type (CI action, Python package, Docker image), gets a full changelog analysis between the old and new versions.
- **Be specific about what affects us** — don't just list changelog items, cross-reference each one against our actual config and code usage.
- **Major version bumps get extra scrutiny** — check for a migration guide. Always fetch it if breaking changes are ambiguous or potentially affect our usage; skip only when all breaking changes are clearly in internal APIs we don't use.
- **Don't merge with failing CI** — if CI fails, investigate and fix first.
- **Grouped updates (Dependabot groups)**: analyze each package in the group separately, then present as one combined report.
- **Preserve existing config** — when making improvements, don't refactor unrelated config. Only touch what's relevant to the update.
- **If you can't fetch release notes** (private repo, deleted releases, etc.), say so explicitly and recommend the user check manually before merging.
- **After merging**: automatically run `/post-merge-cleanup` to sync local branches — do not just remind the user.
