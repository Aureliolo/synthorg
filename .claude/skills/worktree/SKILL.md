---
description: "Manage parallel worktrees: create with prompts, cleanup after merge, status, tree view, rebase"
argument-hint: "<setup|cleanup|status|tree|rebase> [options]"
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
  - AskUserQuestion
  - Task
---

# Worktree Manager

Manage parallel git worktrees for multi-issue development across phases. Handles creation, settings propagation, prompt generation, cleanup after merge, dependency tree view, and status overview.

**Arguments:** "$ARGUMENTS"

---

## Command: `setup`

Create worktrees, copy settings, and generate Claude Code prompts.

### Input formats (3 modes, from most to least explicit)

**Mode 1 — Full explicit:**
```
/worktree setup
feat/delegation-loop-prevention #12,#17 "Delegation + Loop Prevention"
feat/parallel-execution #22 "Parallel Agent Execution"
```

**Mode 2 — Shorthand (issues + description only):**
```
/worktree setup
#12,#17 "Delegation + Loop Prevention"
#22 "Parallel Execution"
```
Branch names auto-generated from description: `feat/delegation-loop-prevention`.
Default branch type prefix is `feat/` unless the user specifies otherwise or the issue labels suggest a different type (e.g., `type:bug` → `fix/`, `type:refactor` → `refactor/`).

**Mode 3 — Issue list only:**

```text
/worktree setup --issues #26,#30,#133,#168
```
Fetches issue titles from GitHub, groups by the user's worktree definitions (or asks for grouping via AskUserQuestion if not provided).

If no definitions are provided at all, ask the user via AskUserQuestion for:
1. How many worktrees to create
2. For each: issues and description (branch names auto-generated)

### Directory naming

Directory suffix is auto-derived from the branch name:
- `feat/delegation-loop-prevention` → `../synthorg-wt-delegation-loop-prevention`
- Strip everything up to and including the first `/` in the branch name (covers `feat/`, `fix/`, `refactor/`, `chore/`, `docs/`, `test/`, `perf/`, `ci/`), then prepend `wt-`
- Repo name extracted from the repository's canonical root metadata (e.g. `basename $(git rev-parse --show-toplevel)`), not the current working directory basename. If running inside a linked worktree, derive the base repo name from shared Git metadata before composing `../<repo-name>-wt-<slug>`

### Steps

1. **Pre-flight checks:**

   a. Verify GitHub CLI is installed and authenticated:

   ```bash
   gh auth status
   ```

   If not authenticated, prompt user to run `gh auth login` first and abort.

   b. Check for uncommitted changes:

   ```bash
   git status --short
   ```

   If dirty, warn and ask via AskUserQuestion whether to proceed or abort.

   c. Ensure on main and up to date. If checkout fails due to dirty working tree, warn and ask whether to stash changes or abort:

   ```bash
   git checkout main
   ```

   If this fails with "Your local changes would be overwritten", ask via AskUserQuestion:
   - **Stash**: `git stash push -m "worktree-setup-autostash"` then `git checkout main`
   - **Abort**: stop setup

   Then pull:

   ```bash
   git pull
   ```

   d. For each worktree definition, verify:
   - Branch doesn't already exist: `git rev-parse --verify --quiet refs/heads/<branch-name>`
   - Directory doesn't already exist: `test -d <dir-path>`
   If either exists, warn and ask how to proceed (skip / reuse / abort).

2. **Check `.claude/` local files exist:**

   ```bash
   test -f .claude/settings.local.json
   ```

   If missing, warn: "No .claude/settings.local.json found — worktrees will prompt for tool permissions." Continue anyway.

3. **For each worktree definition**, run in sequence:

   a. Determine the directory path: `../<repo-name>-wt-<slug>` (e.g. `../synthorg-wt-delegation-loop-prevention`)

   b. Create the worktree. For **new** branches:

   ```bash
   git worktree add -b <branch-name> <dir-path> main
   ```

   For **reuse** (branch already exists from Step 1d):

   ```bash
   git worktree add <dir-path> <branch-name>
   ```

   Note: `-b` creates a new branch and fails if it already exists. The reuse path omits `-b` to attach an existing branch.

   c. Copy all `.claude/` local files (settings, hooks, etc.):

   ```bash
   test -f .claude/settings.local.json && cp .claude/settings.local.json <dir-path>/.claude/settings.local.json
   ```

   Also copy any other `.claude/*.local.*` files if they exist:

   ```bash
   for f in .claude/*.local.*; do test -f "$f" && cp "$f" "<dir-path>/.claude/$(basename "$f")"; done
   ```

4. **Verify all worktrees created:**

   ```bash
   git worktree list
   ```

5. **For each worktree, generate a Claude Code prompt.** The prompt must follow this exact structure:

   a. Fetch each issue's full body from GitHub:
   ```bash
   gh issue view <number> --repo <owner/repo> --json title,body,labels
   ```

   b. **Parse dependencies** from each issue body: look for the `## Dependencies` section, extract `#<number>` references. For each dependency that is a closed issue, note it as satisfied. For open dependencies within this worktree, determine implementation order.

   c. **Match spec labels to source directories.** From each issue's labels, map `spec:*` labels to source directories:
   - `spec:communication` → `src/synthorg/communication/`
   - `spec:agent-system` → `src/synthorg/engine/`, `src/synthorg/core/`
   - `spec:task-workflow` → `src/synthorg/engine/`
   - `spec:company-structure` → `src/synthorg/core/`, `src/synthorg/config/`
   - `spec:templates` → `src/synthorg/templates/`
   - `spec:providers` → `src/synthorg/providers/`
   - `spec:budget` → `src/synthorg/budget/`
   - `spec:tools` → `src/synthorg/tools/`
   - `spec:hr` → `src/synthorg/core/`
   - `spec:human-interaction` → `src/synthorg/api/`, `src/synthorg/cli/`
   - `spec:memory` → `src/synthorg/memory/`
   - `spec:security` → `src/synthorg/security/`
   - `spec:architecture` → `src/synthorg/core/`, `src/synthorg/config/`
   - `spec:providers-scope` → `src/synthorg/providers/`

   **Note:** This mapping is repository-specific. Update it when new `spec:*` labels are added or when the directory structure changes.

   Also read the issue bodies for `§` references to DESIGN_SPEC sections.

   d. **Check which dependency modules exist** on disk (glob the matched directories) to build a concrete file list for the prompt's "Read the relevant source modules" section.

   e. Build the prompt using this template:

   ~~~
   Enter plan mode. You are implementing <phase-description>.

   ## Issues
   - #<N>: <title>
   - #<N>: <title>

   ## Instructions
   1. Read the relevant `docs/design/` pages: <list pages matched from issue spec labels and §section references>
   2. Read the GitHub issues: <gh issue view commands>
   3. Read the relevant source modules: <list directories/files matched from spec labels + dependency parsing>

   ## Scope
   - #<N>: <summarize acceptance criteria from issue body>
   - #<N>: <summarize acceptance criteria from issue body>

   ## Workflow
   - Plan the full implementation before writing any code — present the plan for approval
   - Follow TDD: write tests first, then implement
   - Follow all conventions in CLAUDE.md (immutability, PEP 758, no __future__ imports, structlog logging, etc.)
   - After implementation: create commits on this branch, push with -u, then use /pre-pr-review
   ~~~

   If there are multiple issues in one worktree that have a natural ordering (from dependency parsing or logical sequence), add an `## Implementation order` section.

6. **Present the output** to the user:
   - For each worktree: show the **absolute path** to the worktree directory and a separate `claude` invocation command, followed by the prompt in a code block. Derive the absolute path dynamically by resolving the worktree directory (e.g., using `realpath <dir-path>` or `pwd` from within the worktree) and converting to the platform's native format. On Windows, use backslash paths (e.g., `C:\Users\Aurelio\synthorg-wt-delegation-loop-prevention`).
   - **Note:** The `cd <path> && claude` instruction is for the **user's own terminal** (they will paste it into a new shell). This is NOT a Bash tool invocation — do not confuse with CLAUDE.md's "never use cd in Bash commands" rule, which applies to Bash tool calls within the skill.
   - End with a count: "N worktrees ready. Go."

---

## Command: `cleanup`

Remove worktrees and clean up branches after PRs are merged.

### Steps

1. **List current worktrees:**

   ```bash
   git worktree list
   ```

   If only the main worktree exists, report "No worktrees to clean up." and stop.

2. **Pull latest main:**

   ```bash
   git checkout main && git pull
   ```

3. **Prune remote refs:**

   ```bash
   git fetch --prune
   ```

4. **Check PR merge status** for each non-main worktree's branch individually:

   ```bash
   gh pr list --repo <owner/repo> --head <branch-name> --state all --json state,number --jq '.[0] | {state, number}'
   ```

   For each worktree branch:
   - If PR is **merged**: safe to remove. Proceed.
   - If PR is **open**: warn user — "Branch <name> has open PR #N. Still remove?" Ask via AskUserQuestion.
   - If **no PR found**: warn — "No PR found for <branch>. `git branch -D` will permanently delete any unpushed commits on this branch. Still remove?" Ask via AskUserQuestion.

5. **For each approved worktree**, remove it and track success:

   ```bash
   git worktree remove <path>
   ```

   If removal fails (dirty worktree), warn the user and ask via AskUserQuestion whether to force-remove. Track which worktrees were **successfully removed** — only these branches are eligible for deletion in Step 6.

6. **Delete local feature branches only for successfully removed worktrees.** These are squash-merged so git won't recognize them as merged — use `-D`:

   ```bash
   git branch -D <successfully-removed-branch1> <successfully-removed-branch2> ...
   ```

   Do NOT delete branches for worktrees that failed removal or where the user declined force-remove — this would orphan the worktree.

7. **Verify clean state:**

   ```bash
   git worktree list
   git branch -a
   ```

8. **Report summary:**

   Report: "Clean. Main up to date, N worktrees removed, N branches deleted."

---

## Command: `status`

Show current worktree state and how they compare to main.

### Steps

1. **List worktrees:**

   ```bash
   git worktree list
   ```

2. **For each non-main worktree**, show:
   - Branch name
   - How many commits ahead/behind main:
     ```bash
     git -C <path> rev-list --left-right --count main...<branch>
     ```
   - Whether it has uncommitted changes:
     ```bash
     git -C <path> status --short
     ```

3. **Check for corresponding PRs:**

   ```bash
   gh pr list --repo <owner/repo> --state open --json number,title,headRefName
   ```

   Match each worktree branch to any open PR.

4. **Present a summary table** (show both ahead AND behind counts):

   ```
   Worktree             | Branch                    | vs Main          | PR     | Status
   wt-delegation        | feat/delegation-loop-prev | +5 ahead         | #142   | clean
   wt-parallel          | feat/parallel-execution   | +3 ahead -2 behind | —    | 2 modified
   wt-memory            | feat/memory-layer         | up to date       | #155   | clean
   ```

---

## Command: `tree`

Auto-generate a phase/dependency tree view from a set of issues.

### Input format

```text
/worktree tree --issues #26,#30,#133,#168
```

If no issues specified, try to detect from current worktree branches or ask via AskUserQuestion.

### Steps

1. **Fetch the specified issues:**

   ```bash
   gh issue view <number> --repo <owner/repo> --json number,title,state,labels,body
   ```

2. **Parse dependency graph.** For each issue, extract `## Dependencies` section and resolve `#<N>` references. Build an adjacency list.

3. **Compute tiers** using topological sort:
   - Tier 0: issues with no open internal dependencies (all deps are closed or external)
   - Tier 1: all open internal dependencies are in Tier 0
   - Tier N: all open internal dependencies are in earlier tiers; assign the tier as `1 + max(dependency tier)`
   - Flag circular dependencies as errors. When detected, report the cycle (e.g. "#12 → #17 → #12"), render the remaining non-circular issues in their tiers, and suggest: "Break the cycle by removing one dependency edge, or implement the circular group in a single worktree."

4. **Identify current active worktrees** (if any):
   ```bash
   git worktree list
   ```

5. **Render the tree** showing:
   - Each tier with issues, their state (DONE/OPEN), priority labels
   - Which directories each issue touches (from spec labels)
   - Current worktree assignments (if active)
   - Phase groupings (suggest phases based on tiers + directory conflict analysis)

   Format:
   ```
   ISSUES: 4 total (3/4 done)

   TIER 0 — No dependencies (all prereqs closed)
     ✅ #8   Message bus                    [critical]  communication/
     ✅ #10  Agent-to-agent messaging       [critical]  communication/
     ⬜ #134 Plan-and-Execute loop          [medium]    engine/

   TIER 1 — Depends on Tier 0
     ✅ #12  Hierarchical delegation        [high]      communication/, engine/
     ...
   ```

6. **Show summary:**
   ```
   Done: N/M issues
   Open: X issues across Y tiers
   Suggested next phase: Z parallel worktrees
   ```

---

## Command: `rebase`

Update all worktrees to latest main. Pulls main first, then rebases clean worktrees.

### Steps

1. **Pull main first:**

   ```bash
   git checkout main && git pull
   ```

2. **For each non-main worktree**, first check for uncommitted changes (hard prerequisite):

   ```bash
   git -C <path> status --short
   ```

   If dirty, warn and skip immediately: "Worktree <name> has uncommitted changes — skipping rebase."

3. **Then check ahead/behind status relative to main:**

   ```bash
   git -C <path> rev-list --left-right --count main...<branch>
   ```

   This outputs two tab-separated numbers: `<left>\t<right>` where left = commits on main not on branch (behind), right = commits on branch not on main (ahead).

   - If **0 behind AND 0 ahead**: fully up to date — skip.
   - If **0 behind AND N ahead**: branch is ahead but main hasn't moved — skip (rebase is a no-op, branch already contains everything from main).
   - If **M behind** (regardless of ahead count): this worktree needs rebasing. Warn the user — "Worktree <name> is M commits behind main (and N ahead). Rebase may cause conflicts." Ask via AskUserQuestion: rebase anyway / skip / abort all.

4. **For approved worktrees**, rebase:

   ```bash
   git -C <path> rebase main
   ```

   If rebase fails (conflicts), abort and report:
   ```bash
   git -C <path> rebase --abort
   ```

5. **Verify:**

   ```bash
   git -C <path> log --oneline -1
   ```

6. Report: "N worktrees rebased to <commit>. M skipped (have local commits or dirty state)."

---

## Rules

- **Never force-remove** a worktree without asking the user first.
- **Never delete branches** without checking PR merge status first.
- **Always check `.claude/` local files exist** before copying — warn if missing.
- **Repo name detection**: extract from the repository's canonical root (`basename $(git rev-parse --show-toplevel)`), not the current directory basename. Strip any existing `wt-` prefix to avoid nested names when running from inside a linked worktree.
- **Owner/repo detection**: extract from `git remote get-url origin`.
- **Platform-aware paths**: derive worktree absolute paths dynamically at runtime. On Windows, convert to backslash paths for user-facing output. The `cd <path> && claude` instructions are for the user's own terminal, not Bash tool invocations.
- Worktree directories are always siblings of the main repo directory (`../`).
- When generating prompts, read the actual issue bodies — do not guess or use stale information.
- Parse `spec:*` labels to auto-match source directories for prompt generation.
- Parse `## Dependencies` sections to auto-detect implementation order.
- **Input validation (CRITICAL):** Before interpolating any user-provided value into shell commands, validate:
  - Issue numbers: must match `^[0-9]+$`
  - Branch names: must match `^[a-zA-Z0-9/_.-]+$`
  - Label/filter values: must be a reasonable alphanumeric pattern (no shell metacharacters)
  - Owner/repo (from `git remote`): must match `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$`
  - Directory paths: must not contain shell metacharacters (`;`, `|`, `&`, `$`, `` ` ``, `(`, `)`)
  - Reject and warn if any value fails validation — do not execute the command.
- If `$ARGUMENTS` is empty or doesn't match a command, show a brief usage guide:

  ```text
  /worktree setup <definitions>   — Create worktrees with prompts
  /worktree setup --issues #26,#30  — Issue-aware setup
  /worktree cleanup                — Remove worktrees after merge
  /worktree status                 — Show worktree state
  /worktree tree --issues #26,#30  — Dependency tree view
  /worktree rebase                 — Update worktrees to latest main
  ```
