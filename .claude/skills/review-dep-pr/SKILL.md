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
---

# Review Dependency PR

Comprehensive review of dependency update PRs — whether CI actions, Python packages, Docker images, or anything else. Every dependency update gets a full changelog review because any of them can have new features we should adopt, deprecations to act on, workarounds we can remove, or breaking changes to handle.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: Parse Arguments and Load PRs

1. Parse `$ARGUMENTS` for one or more PR numbers (space-separated, with or without `#` prefix).
2. For each PR, fetch metadata:

   ```bash
   gh pr view <number> --json number,title,body,headRefName,baseRefName,state,mergeable,statusCheckRollup
   ```

3. Also fetch CI status:

   ```bash
   gh pr checks <number> --json name,state
   ```

4. From the PR body (Dependabot format), extract:
   - **Package name** and **ecosystem** (GitHub Actions, pip/uv, Docker, npm, etc.)
   - **Version range**: from → to
   - **Bump type**: major, minor, or patch (infer from semver)
   - **Whether it's a grouped update** (multiple packages in one PR)

If multiple PRs provided, process them all. Collect info for all PRs in parallel, then proceed through the remaining phases for each PR.

## Phase 1: Determine Usage Scope

For each dependency being updated, find where and how we use it:

### GitHub Actions dependencies
Search workflow files:
```bash
# Find all references to the action
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
# For GitHub-hosted deps, fetch releases in the version range
gh api repos/<owner>/<repo>/releases --paginate --jq '.[] | select(.tag_name >= "v<from>" and .tag_name <= "v<to>") | {tag: .tag_name, body: .body}'
```

### Strategy 3: WebFetch
If the PR body has links to release notes or changelogs, fetch them:
- CHANGELOG.md links
- GitHub release page links
- Documentation migration guides (especially for major bumps)

### Strategy 4: WebSearch (fallback)
If release notes are incomplete, search for `"<package> <version> changelog"` or `"<package> migration guide"`.

### For major version bumps: ALWAYS fetch the migration guide
Major bumps almost always have breaking changes. Search for and fetch:
- Migration/upgrade guide
- Breaking changes document
- Any "what's new in vN" blog post

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

For docs-related dependencies, actually build the docs to verify nothing breaks:

```bash
# Checkout the PR branch
git fetch origin <pr-branch>
git checkout <pr-branch>

# Install deps and build
uv sync --group docs
uv run mkdocs build --strict 2>&1
```

If the build fails, capture the errors — they're likely from breaking changes that need fixing.

After checking, return to the original branch:
```bash
git checkout -
```

## Phase 5: Present Findings

For each PR, present a structured report:

### Header
```
## PR #<number>: <title>
**Package**: <name> | **Ecosystem**: <type> | **Bump**: <from> → <to> (<major/minor/patch>)
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

```
"What should we do with PR #<N> (<package> <from>→<to>)?"
```

Options:
- **"Merge as-is"** — No changes needed, changelog reviewed, ship it
- **"Improve and merge"** — Apply the recommended config improvements, then merge (describe what will be changed)
- **"Investigate first"** — Something needs deeper review before deciding (specify what)
- **"Close / Skip"** — Don't want this update (e.g., breaking change not worth the migration)

**If CI is failing on a PR**, replace "Merge as-is" with:
- **"Fix CI and merge"** — Investigate the failure, fix it, then merge

**If multiple PRs are all clean (no actionable items):**

Batch them into one question:
```
"PRs #X, #Y, #Z all look clean after changelog review. Merge all?"
```

Options:
- **"Merge all"** — Ship them all
- **"Let me review individually"** — Break out per-PR decisions
- **"Skip for now"** — Come back later

## Phase 7: Execute Decisions

For each PR based on user's choice:

### Merge as-is
```bash
gh pr merge <number> --squash --auto
```

### Improve and merge
1. Check out the PR branch
2. Make the recommended changes (config improvements, workaround removal, etc.)
3. Commit with descriptive message
4. Push to the PR branch
5. Verify CI passes
6. Merge

### Fix CI and merge
1. Check out the PR branch
2. Investigate the CI failure
3. Fix the issue
4. Commit and push
5. Wait for CI
6. Merge when green

### Close / Skip
```bash
gh pr close <number> --comment "Skipping: <reason from user>"
```

After all merges complete, if any PRs were merged, remind the user to run `/post-merge-cleanup`.

---

## Rules

- **NEVER skip changelog review** — every dependency update, regardless of type (CI action, Python package, Docker image), gets a full changelog analysis between the old and new versions.
- **Be specific about what affects us** — don't just list changelog items, cross-reference each one against our actual config and code usage.
- **Major version bumps get extra scrutiny** — always look for a migration guide.
- **Don't merge with failing CI** — if CI fails, investigate and fix first.
- **Grouped updates (Dependabot groups)**: analyze each package in the group separately, then present as one combined report.
- **Preserve existing config** — when making improvements, don't refactor unrelated config. Only touch what's relevant to the update.
- **If you can't fetch release notes** (private repo, deleted releases, etc.), say so explicitly and recommend the user check manually before merging.
- **After merging**: remind user to run `/post-merge-cleanup` to sync local branches.
