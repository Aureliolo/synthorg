---
description: "Pre-PR review pipeline: automated checks + review agents + fixes + create PR"
argument-hint: "[quick] or [issue number]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - Task
  - AskUserQuestion
  - mcp__github__create_pull_request
---

# Pre-PR Review

Automated pre-PR pipeline that runs checks, launches review agents, triages findings, implements fixes, and creates the PR — so the first push is already reviewed and clean.

**Arguments:** "$ARGUMENTS"

---

## Phase 0: Precondition Checks

1. **Check current branch and handle main:**

   ```bash
   git branch --show-current
   ```

   - If NOT on main: proceed normally.
   - If on main: do NOT abort. Continue to step 2 to detect changes first. If changes exist, ask the user for a branch name via AskUserQuestion (suggest one based on the changes, e.g. `feat/add-cost-tracking`). Then create and switch to that branch:

     ```bash
     git checkout -b <branch-name>
     ```

     Uncommitted/staged/untracked changes carry over to the new branch automatically.

2. **Check for changes.** Collect uncommitted, staged, untracked, and committed-but-unpushed changes:

   ```bash
   # Uncommitted changes (modified tracked files)
   git diff --name-only
   # Staged changes
   git diff --staged --name-only
   # Untracked files (new files not yet added to git)
   git ls-files --others --exclude-standard

   # Committed but not pushed (compared to main)
   git diff main...HEAD --name-only
   ```

   Combine all four lists (deduplicated) as the full set of changed files.

   If no changes at all, abort with: "No changes detected. Nothing to review."

3. **Check if a PR already exists for this branch:**

   ```bash
   gh pr list --head "$(git branch --show-current)" --json number,title,url --jq '.[0]'
   ```

   If a PR exists, ask the user via AskUserQuestion:
   - Option A: "Run review + push to existing PR #N"
   - Option B: "Redirect to /aurelio-review-pr (for external feedback triage)"

   If user picks B, stop and tell them to run `/aurelio-review-pr`.

4. **Check if branch is behind main:**

   ```bash
   git rev-list --count HEAD..main
   ```

   If behind, warn: "Branch is N commits behind main. Consider rebasing before review."

5. **Collect changed files into categories** from the combined list of all changes (uncommitted + staged + untracked + committed-but-unpushed):

   - `src_py`: `.py` files in `src/`
   - `test_py`: `.py` files in `tests/`
   - `config`: `.toml`, `.yaml`, `.json`, `.cfg` files
   - `docs`: `.md` files
   - `other`: everything else

6. **Large diff warning.** If 50+ files changed, warn about token cost and ask user whether to proceed with all agents or select a subset.

## Phase 1: Quick Mode Detection

Determine if agent review can be skipped:

- If `$ARGUMENTS` contains `quick` -> skip agents, go to Phase 2 then Phase 8, then Phase 10 and Phase 11
- **Auto-detect**: If ALL changed files are non-substantive (only `.md` docs, config formatting, typo-level edits with no logic changes), skip agents automatically
  - Auto-skip examples: all changes are `.md` files; only `pyproject.toml` version bump; only `.yaml`/`.json` config with no Python changes
  - Do NOT auto-skip: any `.py` file changed; config changes that affect runtime behavior; new dependencies added
- If auto-skipping, inform user: "Skipping agent review (no substantive code changes detected). Running automated checks only."

## Phase 2: Automated Checks (always run)

**Scoping:** If no `.py` files changed (only `.md`, `.yaml`, `.toml`, `.json`, etc.), skip steps 1-5 entirely — ruff, mypy, and pytest only operate on Python files and running them is unnecessary for docs/config-only changes.

Run these sequentially, fixing as we go:

1. **Lint + auto-fix:**

   ```bash
   uv run ruff check src/ tests/ --fix
   ```

2. **Format:**

   ```bash
   uv run ruff format src/ tests/
   ```

3. If steps 1-2 changed any files, stage them:

   ```bash
   git add -A
   ```

4. **Type-check:**

   ```bash
   uv run mypy src/ tests/
   ```

5. **Test + coverage:**

   ```bash
   uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80
   ```

**Failure handling:**
- If mypy fails: fix the type errors, re-run mypy
- If pytest fails: fix failing tests, re-run pytest
- If something can't be auto-fixed: present the error to the user via AskUserQuestion, ask how to proceed (fix now / skip check / abort)
- After fixing, stage changes with `git add -A`

**If in quick mode:** After automated checks pass, skip directly to Phase 8 (Post-Fix Verification), then continue to Phase 10 (Commit + Push + Create PR) and Phase 11 (Summary).

## Phase 3: Determine Agent Roster

Based on changed files and diff content, select which agents to launch. Stage all changes first, then get a unified diff against main:

```bash
# Stage everything so the diff includes uncommitted + untracked changes
git add -A
# Unified diff: all changes (committed + staged) vs main
git diff --staged main
```

This captures committed-but-unpushed changes AND any uncommitted/untracked work in a single diff.

| Agent | Condition | subagent_type |
|---|---|---|
| **code-reviewer** | Any `src_py` or `test_py` | `pr-review-toolkit:code-reviewer` |
| **python-reviewer** | Any `src_py` or `test_py` | `everything-claude-code:python-reviewer` |
| **pr-test-analyzer** | `test_py` changed, OR `src_py` changed with no corresponding test changes | `pr-review-toolkit:pr-test-analyzer` |
| **silent-failure-hunter** | Diff contains `try`, `except`, `raise`, error handling patterns | `pr-review-toolkit:silent-failure-hunter` |
| **comment-analyzer** | Diff contains docstring changes (`"""`) or significant comment changes | `pr-review-toolkit:comment-analyzer` |
| **type-design-analyzer** | Diff contains `class ` definitions, `BaseModel`, `TypedDict`, type aliases | `pr-review-toolkit:type-design-analyzer` |
| **logging-audit** | Any `src_py` changed | `pr-review-toolkit:code-reviewer` (custom prompt below) |
| **resilience-audit** | Any `src_py` changed | `pr-review-toolkit:code-reviewer` (custom prompt below) |
| **security-reviewer** | Files in `src/ai_company/api/`, `src/ai_company/security/`, `src/ai_company/tools/`, `src/ai_company/config/` changed, OR diff contains `subprocess`, `eval`, `exec`, `pickle`, `yaml.load`, auth/credential patterns | `everything-claude-code:security-reviewer` |
| **docs-consistency** | **ALWAYS** — runs on every PR regardless of change type | `pr-review-toolkit:code-reviewer` (custom prompt below) |

### Docs-consistency custom prompt

The docs-consistency agent ensures project documentation never drifts from the codebase. It runs on **every PR** — code changes, config changes, docs-only changes, all of them.

**What to check:**

Read the current `DESIGN_SPEC.md`, `CLAUDE.md`, and `README.md` in full. Then compare them against the PR diff and the actual current state of the codebase. Flag anything that is now inaccurate, incomplete, or missing.

**DESIGN_SPEC.md (CRITICAL — this is the project's source of truth):**
1. §15.3 Project Structure — does it match the actual files/directories under `src/ai_company/`? Any new modules missing? Any listed files that no longer exist? (CRITICAL)
2. §3.1 Agent Identity Card — does the config/runtime split documentation match the actual model code? (MAJOR)
3. §15.4 Key Design Decisions — are technology choices and rationale still accurate? (MAJOR)
4. §15.5 Pydantic Model Conventions — do the documented conventions match how models are actually written in code? Are "Adopted" vs "Planned" labels still accurate? (MAJOR)
5. §10.2 Cost Tracking — does the implementation note match the actual `TokenUsage` and spending summary models? (MAJOR)
6. §11.1.1 Tool Execution Model — does it match actual `ToolInvoker` behavior? (MAJOR)
7. §15.2 Technology Stack — are versions, libraries, and rationale current? (MEDIUM)
8. §9.2 Provider Configuration — are model IDs, provider capability examples, and config/runtime mapping still representative? (MEDIUM)
9. §9.3 LiteLLM Integration — does the integration status match reality? (MEDIUM)
10. Any other section that describes behavior, structure, or patterns that have changed (MAJOR)

**CLAUDE.md (CRITICAL — this guides all future development):**
11. Code Conventions — do documented patterns match what's actually in the code? New patterns used but not documented? Documented patterns no longer followed? (CRITICAL)
12. Logging section — are event import paths, logger patterns, and rules accurate? (CRITICAL)
13. Resilience section — does it match the actual retry/rate-limit implementation? (MAJOR)
14. Package Structure — does it match the actual directory layout? (MAJOR)
15. Testing section — are markers, commands, and conventions current? (MEDIUM)
16. Any other section that gives instructions that don't match reality (CRITICAL)

**README.md:**
17. Installation, usage, and getting-started instructions — still accurate? (MAJOR)
18. Feature descriptions — do they match what's actually built? (MEDIUM)
19. Links — any dead links or references to things that moved? (MINOR)

**Key principle:** It is better to flag a false positive than to let documentation drift silently. When in doubt, flag it.

### Logging-audit custom prompt

The logging-audit agent must check for these violations (from CLAUDE.md `## Logging`):

**Infrastructure violations (hard rules):**
1. `import logging` + `logging.getLogger` in application source (CRITICAL)
2. `print()` calls in application source (CRITICAL)
3. Logger variable named `_logger` instead of `logger` (CRITICAL)
4. Log calls using positional `%s` formatting instead of structured kwargs (CRITICAL)
5. Log call event argument is a bare string literal, not an event constant (MAJOR)
6. Business logic file missing a `logger = get_logger(__name__)` declaration (MAJOR)

**Logging coverage suggestions (soft rules — mark as SUGGESTION, must be validated by user in triage):**

For every function touched by the changes, analyze its logic and suggest missing logging where appropriate:

1. Error/except paths that don't `logger.warning()` or `logger.error()` with context before raising or returning (SUGGESTION)
2. State transitions (status changes, lifecycle events, mode switches) that don't `logger.info()` (SUGGESTION)
3. Object creation, entry/exit of key functions, or important branching decisions that don't `logger.debug()` (SUGGESTION)
4. Any other code path that would benefit from logging for debuggability or operational visibility (SUGGESTION)

**Exclusions — do NOT flag these for coverage suggestions:**
- Pure data models, Pydantic `BaseModel` subclasses, enums, TypedDict definitions
- Re-export `__init__.py` files
- Simple property accessors, trivial getters/setters
- One-liner functions with no branching or side effects
- Test files

### Resilience-audit custom prompt

The resilience-audit agent must check for these violations (from CLAUDE.md `## Resilience`).

Resilience is a cross-cutting concern — ANY code can introduce resilience issues, not just provider files. Check all changed source files.

**Hard rules (provider layer):**
1. Driver subclass implements its own retry/backoff logic instead of relying on base class (CRITICAL)
2. Calling code wraps provider calls in manual retry loops (CRITICAL)
3. New `BaseCompletionProvider` subclass doesn't pass `retry_handler`/`rate_limiter` to `super().__init__()` (MAJOR)
4. Retryable error type created without `is_retryable = True` (MAJOR)
5. `asyncio.sleep` used for retry delays outside of `RetryHandler` (MAJOR)

**Hard rules (any code):**
6. Error hierarchy overlap — new exception classes that accidentally inherit from or shadow `ProviderError`, which could cause incorrect error routing (MAJOR)
7. Code that catches broad `Exception` or `BaseException` and silently swallows provider errors that should propagate (MAJOR)
8. Manual retry/backoff patterns (e.g., `for attempt in range(...)`, `while retries > 0`, `time.sleep` in loops) anywhere in the codebase — retries belong in `RetryHandler` only (CRITICAL)

**Soft rules (SUGGESTION):**
9. New error types missing `is_retryable` classification when they represent I/O or network failures (SUGGESTION)
10. Provider call site that catches `ProviderError` but doesn't account for `RetryExhaustedError` (SUGGESTION)
11. Engine or orchestration code that imports from `providers/` without considering that provider calls may raise `RetryExhaustedError` (SUGGESTION)
12. Non-retryable error types (e.g., deterministic failures like bad templates, invalid config) that should NOT be retryable — verify they don't accidentally inherit retryable classification (SUGGESTION)

## Phase 4: Launch Review Agents (parallel)

Launch ALL selected agents **in parallel** using the Task tool. **Do NOT use `run_in_background`** — launch them as regular parallel Task calls so results arrive together.

Each agent receives:
- List of changed files
- The diff content for those files
- Relevant CLAUDE.md sections (Logging, Resilience, Code Conventions, Testing)

If an agent times out or fails, proceed with findings from the agents that succeeded. Report the failed agent in the summary.

Collect all findings with their severity/confidence scores.

## Phase 5: Consolidate and Triage

Build a single consolidated table of ALL findings from all agents.

For each item, determine:

- **Source**: Which agent found it
- **Severity**: Critical / Major / Medium / Minor
  - Map confidence 91-100 to Critical, 80-90 to Major, 60-79 to Medium, below 60 to Minor
- **File:Line**: Where the issue is
- **Issue**: One-line summary of the problem
- **Valid?**: Your assessment — is this correct advice for this codebase? Check against CLAUDE.md rules and actual code

**Deduplication:** If multiple agents flag the same issue at the same location, merge into one item and note all sources.

**Conflict detection:** If two agents contradict each other, flag both positions.

Sort by severity (Critical first, Minor last).

## Phase 6: Present for User Approval

Show the consolidated table with:
- Total count of items
- Count by source agent

**Default behavior: implement ALL valid findings** — including pre-existing issues found in surrounding code, suggestions, and anything the agents correctly identified. Do NOT skip items just because they are "pre-existing" or "out of scope" — if an agent found a valid issue in code touched by (or adjacent to) this PR, fix it now.

Ask the user via AskUserQuestion:
- "Implement all (Recommended)" — this is the default
- "Let me review the list first"
- "Skip some items"

If the user wants to skip items, ask which ones by number.

## Phase 7: Implement Fixes

For each approved item, grouped by file (minimize context switches):

1. Read the file
2. Apply the fix
3. Move to the next fix in the same file before switching files

After all fixes:
- If a fix requires test changes, change the tests too
- If a fix introduces new code paths, add test coverage

## Phase 8: Post-Fix Verification

Run the full automated checks again:

1. `uv run ruff check src/ tests/`
2. `uv run ruff format src/ tests/`
3. `uv run mypy src/ tests/`
4. `uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80`

If anything fails, fix and re-run. Stage all changes after passing:

```bash
git add -A
```

## Phase 9: Polish Pass (code-simplifier)

**Skip this phase if:** quick mode was used, OR no agent findings were implemented (nothing changed beyond Phase 2 auto-fixes).

1. Launch `pr-review-toolkit:code-simplifier` on all modified files
2. If it suggests improvements, apply them
3. Re-run `uv run ruff check src/ tests/` + `uv run ruff format src/ tests/` to ensure polish didn't break formatting

## Phase 10: Commit + Push + Create PR

1. **Stage all files:**

   ```bash
   git add -A
   ```

2. **Commit** with a descriptive message:
   - Type based on changes (feat/fix/refactor/docs/etc.)
   - If agents ran, add body: "Pre-reviewed by N agents, M findings addressed"

3. **Push** with `-u` flag:

   ```bash
   git push -u origin "$(git branch --show-current)"
   ```

4. **If PR already exists** (detected in Phase 0): push only, do NOT create a new PR.

5. **If no PR exists**, create one using the **`mcp__github__create_pull_request`** tool (NOT `gh pr create` — that is blocked by hookify):

   Parameters:
   - `owner`: repo owner (from `git remote get-url origin`)
   - `repo`: repo name
   - `title`: PR title
   - `head`: current branch name
   - `base`: main
   - `body`: PR description

   PR body should include:
   - Summary bullets of what changed
   - Test plan
   - Review coverage note (agents run, findings count)
   - Issue linkage (`closes #N`) if user provided an issue number as argument or commits reference one

6. **If commit fails due to pre-commit hooks:** fix the issue and create a NEW commit. **NEVER use `--no-verify`.**

## Phase 11: Summary

Report:
- **Agents run**: count + names
- **Findings**: total, by severity, by source
- **Findings fixed** vs skipped
- **Files modified**
- **Test results**: pass/fail, coverage %
- **PR URL** (or "pushed to existing PR #N")
- **Reminder**: "Run `/aurelio-review-pr` after external reviewers provide feedback"

---

## Rules

- Never skip a fix without telling the user why.
- If a fix requires changing tests, change the tests too.
- If a fix introduces new code paths, add test coverage.
- Group file edits to minimize re-reading files.
- Respect all rules in CLAUDE.md (formatting, logging, no placeholders, etc.).
- If two agents contradict each other, flag it and ask the user.
- Do NOT use `--no-verify` or `--amend` for commits.
- Agent failures are non-fatal — proceed with available findings, report failed agents.
- **Fix everything valid — never defer, never skip.** Every valid recommendation must be implemented — including pre-existing issues, suggestions, and findings in surrounding code. No creating GitHub issues for "too large" items, no deferring to future PRs, no marking things as "out of scope".
