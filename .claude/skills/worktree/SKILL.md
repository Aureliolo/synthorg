---
description: "Manage parallel worktrees: create with prompts, cleanup after merge, status, tree view, rebase, launch in Windows Terminal tabs"
argument-hint: "<setup|cleanup|status|tree|rebase|launch> [options]"
allowed-tools:
  - Bash
  - Read
  - Write
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

**Mode 1: Full explicit:**

```text
/worktree setup
feat/delegation-loop-prevention #12,#17 "Delegation + Loop Prevention"
feat/parallel-execution #22 "Parallel Agent Execution"
```

**Mode 2: Shorthand (issues + description only):**

```text
/worktree setup
#12,#17 "Delegation + Loop Prevention"
#22 "Parallel Execution"
```
Branch names auto-generated from description: `feat/delegation-loop-prevention`.
Default branch type prefix is `feat/` unless the user specifies otherwise or the issue labels suggest a different type (e.g., `type:bug` → `fix/`, `type:refactor` → `refactor/`).

**Mode 3: Issue list only:**

```text
/worktree setup --issues #26,#30,#133,#168
```
Fetches issue titles from GitHub, groups by the user's worktree definitions (or asks for grouping via AskUserQuestion if not provided).

If no definitions are provided at all, ask the user via AskUserQuestion for:
1. How many worktrees to create
2. For each: issues and description (branch names auto-generated)

**Mode 4: Description only (no issues):**

```text
/worktree setup "improve setup wizard UX"
```

Creates a worktree from the description alone: branch name auto-generated (`feat/improve-setup-wizard-ux`), no issue fetching, no prompt generation. Useful for exploratory work, ad-hoc improvements, or tasks without a GitHub issue. All other setup steps (pre-flight, helper detection, settings copy, dependency sync) still apply. Skip steps 6 through 8 (prompt generation, auto-launch, output); just report the worktree path and `cd <path> && claude` command.

### Flag: `--no-launch`

Append `--no-launch` to any of the modes above to suppress post-setup launching:

```text
/worktree setup --no-launch "improve setup wizard UX"
/worktree setup --issues #26,#30,#133 --no-launch
```

When set, the skill creates the worktree(s) and prints the generated prompts (if any), but skips step 7 (`wt.exe` tab opening) entirely and adjusts the step 8 footer accordingly. Useful when you want the worktrees to exist but plan to open them later with `/worktree launch` or by hand.

This flag is independent of the `wt-synthorg.ps1` helper's `-NoLaunch` flag: when the skill delegates to the helper (step 4 path A) it ALWAYS passes `-NoLaunch` so the helper never grabs the user's shell. The skill's own `--no-launch` only controls whether the skill itself opens `wt.exe` tabs after the worktrees exist.

### Directory naming

Directory suffix is auto-derived from the branch name. Produce a bare `<slug>` (NO `wt-` prefix); the `wt-` prefix is added exclusively by the directory-path template:
- Example: branch `feat/delegation-loop-prevention` → slug `delegation-loop-prevention` → directory `../synthorg-wt-delegation-loop-prevention`
- Slug derivation: strip everything up to and including the first `/` in the branch name (covers `feat/`, `fix/`, `refactor/`, `chore/`, `docs/`, `test/`, `perf/`, `ci/`). Then **replace any remaining `/` characters with `-`** so nested branches like `feat/foo/bar` become slug `foo-bar` (never `foo/bar`). The slug must match `^[a-zA-Z0-9._-]+$` after derivation; reject and abort if it does not.
- Directory template: `../<repo-name>-wt-<slug>` where `<slug>` is the bare derived slug (no `wt-` prefix on the slug itself). The `wt-` in the template is the only source of that prefix, so there is never a double prefix.
- Repo name extracted from the repository's canonical root metadata (e.g. `basename $(git rev-parse --show-toplevel)`), not the current working directory basename. If running inside a linked worktree, derive the base repo name from shared Git metadata before composing `../<repo-name>-wt-<slug>`.

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

   Then fetch and fast-forward (refuses to merge if local `main` diverged, surfacing the divergence loudly instead of silently merging). Pass `origin main` explicitly rather than relying on the current branch's upstream:

   ```bash
   git fetch origin main && git pull --ff-only origin main
   ```

   d. For each worktree definition, verify:
   - Branch doesn't already exist: `git rev-parse --verify --quiet refs/heads/<branch-name>`
   - Directory doesn't already exist: `test -d <dir-path>`
   If either exists, warn and ask how to proceed (skip / reuse / abort).

2. **Check `.claude/` local files exist:**

   ```bash
   test -f .claude/settings.local.json
   ```

   If missing, warn: "No .claude/settings.local.json found. Worktrees will prompt for tool permissions." Continue anyway.

3. **Detect the `wt-synthorg.ps1` helper** (one-shot, before the per-worktree loop):

   ```bash
   where.exe wt-synthorg.ps1 2>&1 | head -1
   ```

   - Output ends with `wt-synthorg.ps1`: the script is on PATH. Delegate per-worktree mechanical setup to it (path A below).
   - Output is `INFO: Could not find files...` (or empty): the script is missing. Use the inline path (path B below).

   Stream-only redirects (`2>&1`) are used here instead of `>/dev/null` because some sessions have a hook that blocks any `>` token, even when redirecting to `/dev/null`.

   Both paths must run **sequentially across worktrees** to avoid uv/npm/go cache-lock contention.

4. **For each worktree definition**, run in sequence:

   a. Determine the directory path: `../<repo-name>-wt-<slug>` (e.g. `../synthorg-wt-delegation-loop-prevention`)

   b. **Path A (helper present)**: one call per worktree.

   Before building the command, re-validate the two interpolated values at the invocation site (defence in depth; the "Directory naming" and "Input validation (CRITICAL)" rules already cover this upstream, but agents may reach step 4 without having read them):

   - `<slug>` must match `^[a-zA-Z0-9._-]+$`.
   - `<type>` must match `^[a-z]+$` (lowercase prefix like `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`, `ci`).

   If either fails validation, reject and abort the whole setup; do not interpolate into shell.

   Probe for `pwsh` once before the per-worktree loop (skip if already done):

   ```bash
   command -v pwsh 2>&1 | grep -q . && echo pwsh-found || echo pwsh-missing
   ```

   `pwsh-missing` means PowerShell 7 isn't on PATH even though the `.ps1` is. Fall through to path B (inline) for every worktree rather than emitting broken commands.

   When `pwsh-found`, invoke the helper per worktree:

   ```bash
   pwsh -NoProfile -Command "wt-synthorg -Name '<slug>' -BranchType '<type>' -NonInteractive -NoLaunch"
   ```

   `<slug>` is the bare derived slug (no `wt-` prefix), `<type>` is the branch type prefix (`feat`, `fix`, `refactor`, etc., as derived from issue labels or user input). The helper handles: dirty-tree guard, branch-base from `origin/main`, `.claude/*.local.*` copy, `uv sync`, `npm ci` (web), `go mod download` (cli). It does NOT cd or launch claude (because of `-NoLaunch`).

   Always pass `-NonInteractive -NoLaunch` regardless of any skill flags: the skill keeps full control over post-setup launching.

   **Error handling:** if the `pwsh` command exits non-zero (helper aborted: dirty target dir conflict it could not auto-resolve, network failure during fetch, dep-sync failure, etc.), do not silently continue to the next worktree. Surface the failure to the user via AskUserQuestion with three options: (a) retry the helper, (b) fall back to path B (inline) for this worktree only, (c) abort the whole setup.

   Skip the rest of step 4 (c, d) for this worktree and continue to the next one. After all worktrees are created, continue to step 5.

   c. **Path B (inline fallback)**: create the worktree manually. For **new** branches:

   ```bash
   git worktree add -b <branch-name> <dir-path> origin/main
   ```

   For **reuse** (branch already exists from Step 1d):

   ```bash
   git worktree add <dir-path> <branch-name>
   ```

   Note: `-b` creates a new branch and fails if it already exists. The reuse path omits `-b` to attach an existing branch. Branching from `origin/main` (not local `main`) uses the just-fetched remote ref directly.

   Then copy all `.claude/` local files (settings, hooks, etc.):

   ```bash
   for f in .claude/*.local.*; do test -f "$f" && cp "$f" "<dir-path>/.claude/$(basename "$f")"; done
   ```

   d. **Pre-sync all dependencies** (inline path only; the helper already did this in path A).

   **Python:**

   ```bash
   uv sync --project <dir-path>
   ```

   **Node.js (web dashboard):**

   ```bash
   npm --prefix <dir-path>/web ci --silent
   ```

   **Go (CLI):**

   ```bash
   go -C <dir-path>/cli mod download
   ```

   **IMPORTANT:** Never use `cd` to change into the worktree directory; use `--project`, `--prefix`, or `-C` flags instead. `cd` poisons the shell cwd for all subsequent Bash calls.

5. **Verify all worktrees created:**

   ```bash
   git worktree list
   ```

6. **For each worktree, generate a Claude Code prompt.** The prompt must follow this exact structure:

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
   - Plan the full implementation before writing any code; present the plan for approval
   - **Write the plan incrementally in small sections, not as one huge file.** Large plan files repeatedly fail with partial / truncated responses. Build the plan section-by-section:
     1. Start with a short outline (section titles only, no body).
     2. Write each section as a separate small Write/Edit call (one section per tool call, <= ~150 lines per section).
     3. Prefer many small sections over one long document. Typical sections: Goal, Scope, Files to touch, Phases (each phase its own section), Tests, Acceptance, Risks, Out-of-scope.
     4. If a section is still too large, split it further (e.g. one section per phase, one section per subsystem). Never attempt a single multi-thousand-line Write.
   - Follow TDD: write tests first, then implement
   - Follow all conventions in CLAUDE.md (immutability, PEP 758, no __future__ imports, structlog logging, etc.)
   - After implementation: create commits on this branch, push with -u, then use /pre-pr-review

   ## Decision Protocol (MANDATORY)
   - For ANY major decision (architecture choice, library inclusion, scope change, feature toggle, data model design, API shape, whether to include/exclude something), use the AskUserQuestion tool to ask the user. NEVER make big decisions autonomously.
   - Present options with pros AND cons. Let the user decide.
   - Examples of what requires AskUserQuestion: "Should we use X or Y approach?", "Should this feature include Z?", "The spec says X but Y might be better; which do you prefer?", "This touches N files; should we split into separate PRs?"

   ## Quality Standards (MANDATORY)
   - Build BEST IN CLASS. No shortcuts, no "good enough", no "we can improve later".
   - NEVER defer anything. If a finding says "fix X", fix X completely. No TODOs, no stubs, no "phase 2" thinking.
   - Still alpha: breaking API/interface changes are fine, no compat shims. BUT schema changes MUST ship a matching yoyo revision file under `src/synthorg/persistence/<backend>/revisions/` (never edit an existing revision; always author a new `<14-digit-ts>_<name>.sql` file) so migration paths stay testable.
   - Every piece of work must meet the highest standard of security, UX, maintainability, and correctness.

   ## Do-not-introduce
   These categories are guarded by a mix of standing gates (pre-commit / pre-push / `/pre-pr-review`) and reviewer enforcement. Rows marked "planned gate" are reviewer-enforced today and become standing gates when their scripts land; rows without that marker already have a standing gate in place. Don't write any of these in the first place.
   - Bare `Exception` / `RuntimeError` raises in domain code -- use a `<Domain><Condition>Error` subclass of `DomainError` registered in `src/synthorg/api/exception_handlers.py` (planned gate `scripts/check_domain_error_hierarchy.py`, tracked by #1738; until that script lands, reviewer-enforced).
   - Magic numbers in scoring / threshold / timeout / retry contexts -- name the constant in the relevant module or settings registry (planned gate `scripts/check_no_magic_numbers.py`, tracked by #1739; until that script lands, reviewer-enforced).
   - Settings consumed by services that aren't started at boot -- wire the consumer through `src/synthorg/api/lifecycle_helpers.py` (gated by `scripts/check_setting_to_startup_trace.py`).
   - `Mock()` / `AsyncMock()` / `MagicMock()` without `spec=ConcreteClass` -- always pass `spec=` (gated by `scripts/check_mock_spec.py`).
   - `import logging` / `logging.getLogger(...)` / `print(...)` in application code -- use `from synthorg.observability import get_logger` and structured kwargs (gated by pre-commit + reviewer checks).
   - `cd <dir> && <cmd>` prefixes in Bash commands or `git -C <cwd> ...` to the current working directory -- use the tool's native `-C` / `--prefix` / `--project` flag, or `bash -c "cd <dir> && <cmd>"` for tools without one (gated by hookify `no-cd-prefix` and `scripts/check_git_c_cwd.sh`).
   ~~~

   If there are multiple issues in one worktree that have a natural ordering (from dependency parsing or logical sequence), add an `## Implementation order` section.

7. **Auto-launch tabs if running in Windows Terminal.** Skip this step entirely if the user passed `--no-launch` (see "Input formats"). Check `$WT_SESSION` and `wt.exe` availability:

   ```bash
   test -n "$WT_SESSION" && which wt.exe 2>&1 | grep -q "wt\.exe$" && echo "auto-launch-ok" || echo "manual"
   ```

   If `auto-launch-ok`, spawn one tab per worktree sequentially (same invocation as the `launch` subcommand, no trailing command so the user's default profile loads normally):

   ```bash
   wt.exe -w 0 new-tab --title "<slug>" -d "<forward-slash-path>"
   ```

   Use forward-slash paths (`C:/Users/Aurelio/synthorg-wt-<slug>`) to avoid Bash interpreting `\t` / `\U` / `\N` as escape sequences. `-w 0` targets the current Windows Terminal window. No trailing command. See the "Design note (do NOT regress)" block under the `launch` subcommand below for why.

   If not in Windows Terminal or `wt.exe` is missing, skip auto-launch and instead tell the user to run `cd <path> && claude` manually in each target terminal.

8. **Present the output** to the user by printing each worktree's prompt INLINE in chat as a copy-pasteable fenced code block. Do NOT write the prompt to a file; the user copies directly from chat.

   **Fence nesting policy:** the prompt body generated in step 6e may itself contain triple-backtick code blocks (e.g. `` ```bash `` examples pulled from an issue body). To avoid the outer fence closing prematurely, choose an outer fence marker that never appears inside the body. In practice:

   - Scan the generated prompt body for the longest run of consecutive backticks (`longest_backticks`). Use an outer fence of `longest_backticks + 1` backticks (minimum 4 backticks). Include the `text` language tag on the outer fence so markdown lints are happy (e.g. ` ````text ... ```` `).
   - An acceptable fallback is a tilde outer fence (`~~~text ... ~~~`) since the prompt body will not contain tilde fences. Both are valid CommonMark; pick whichever renders cleanly in the chat UI.
   - **Never** use plain ` ```text ` as the outer wrapper; any inner `` ``` `` in the prompt body will close it.

   Format per worktree (tilde-fence example; swap for 4+ backticks if preferred):

   ~~~text
   ### <slug>

   **Path:** `C:\Users\Aurelio\synthorg-wt-<slug>`

   Prompt to paste into the claude REPL (after running `claude` in the tab):

   ```text
   <full prompt body as generated in step 6e; inner ``` blocks are safe because the outer fence is tildes>
   ```
   ~~~

   Then, at the very end, a one-line status:

   - Auto-launched: "N tabs opened in Windows Terminal. In each tab run `claude` and paste the corresponding prompt above."
   - Manual: "N worktrees ready. In each tab run `cd <path> && claude` and paste the corresponding prompt above."
   - `--no-launch` mode: "N worktrees ready. No tabs opened (--no-launch). Run `cd <path> && claude` when you want to start, then paste the corresponding prompt above."

   **Do not save prompts to disk**; the generated content is ephemeral scaffolding the user adapts on paste. Saving to `.claude/initial-prompt.md` creates stale artifacts that drift from what the user actually submitted.

---

## Command: `launch`

Open each worktree as a **plain terminal tab** in the current Windows Terminal window. Each tab uses the user's default profile (PowerShell, Git Bash, whatever they normally use) and lands in the worktree directory. The user then manually runs `claude` in each tab. Windows-only.

**Design note (do NOT regress):** Do not pass `claude`, `bash launch.sh`, `pwsh -NoExit -File ...`, or any other command as the wt `new-tab` command. Any command replaces the profile's default commandline, changes the tab's process icon, and breaks the "looks like a normal terminal" expectation that the user explicitly required. Always pass only `-d <path>` (plus `-w 0` and `--title`). This was validated by testing `-d <path>` vs `-d <path> claude` vs `-d <path> bash -c 'claude; exec bash'`. Only the bare `-d <path>` form produced a tab indistinguishable from a manually-opened one.

### Input format

```text
/worktree launch              # open a tab for every non-main worktree
/worktree launch <name>       # open just one by dir-suffix (e.g. "tighten-workflow-permissions")
```

**Input validation:** `<name>` flows into shell commands (the `wt.exe --title` argument and the path-suffix filter). Before using `<name>`, validate it under the general "Input validation" rules at the bottom of this file (see `## Rules` -> `Input validation (CRITICAL)`). In particular, reject any value containing shell metacharacters (`;`, `|`, `&`, `$`, `` ` ``, `(`, `)`), whitespace, or path separators (`/`, `\`). A practical allowlist: `^[a-zA-Z0-9_.-]+$`. Reject and warn if validation fails -- do not execute `wt.exe` with an unvalidated `<name>`.

### Pre-flight

1. **Detect Windows Terminal:**

   ```bash
   test -n "$WT_SESSION" && echo "in-wt" || echo "not-in-wt"
   which wt.exe 2>&1 | grep -q "wt\.exe$" || echo "wt-missing"
   ```

   - If `wt.exe` is missing: report "Windows Terminal not installed or not on PATH. Cannot launch." and stop.
   - If `WT_SESSION` is unset: warn "Not running inside Windows Terminal. Tabs will open in a new window." Ask via AskUserQuestion whether to continue.

2. **List worktrees (excluding main):**

   ```bash
   git worktree list --porcelain
   ```

   Parse into `(path, branch)` pairs. Skip the main worktree (matches `git rev-parse --show-toplevel`). If filtering by `<name>`, keep only worktrees whose path suffix matches.

### Steps

1. **For each worktree**, derive:
   - Path in **forward-slash form** (e.g. `C:/Users/Aurelio/synthorg-wt-<slug>`). Windows Terminal's `-d` flag accepts forward slashes natively and this avoids shell escape-sequence risk (e.g. `\t` in a Bash double-quoted string would be interpreted as a tab character, and `\U` / `\N` can trigger warnings or unicode-escape behavior in some shells). Do NOT emit backslash paths from the Bash tool.
   - Tab title: the dir suffix after `wt-` (e.g. `tighten-workflow-permissions`), truncated to ~30 chars.

2. **Spawn one tab per worktree, sequentially:**

   ```bash
   wt.exe -w 0 new-tab --title "<title>" -d "<forward-slash-path>"
   ```

   - `-w 0` targets the current Windows Terminal window (adds a tab, does not open a new window)
   - `-d <path>` sets the tab's starting directory. Forward slashes work; wt accepts them and the shell never rewrites them.
   - No trailing command. The default profile starts naturally.
   - Keep calls sequential (one Bash call per worktree). Each call returns exit 0 immediately; wt fires-and-forgets.

3. **Report:**

   ```text
   N tabs opened: <slug1>, <slug2>, ...
   In each tab, run: claude
   ```

   Note: `launch` does not know the prompt content; that is generated fresh by `setup`. If the user ran `/worktree launch` standalone (without a preceding `/worktree setup` in the same chat turn), the tab opens empty and the user types their own prompt. Do not tell them to paste from any file; the skill no longer writes prompts to disk.

### Platform note

Non-Windows (macOS, Linux) have different terminal-tab spawning mechanisms (`osascript` for iTerm2 / Terminal.app, `gnome-terminal --tab`, `kitty @ launch --type=tab`, etc.). This subcommand is Windows Terminal only for now. On other platforms, fall back to printing the `cd <path>` commands so the user can do it manually.

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
   - If PR is **open**: warn user: "Branch <name> has open PR #N. Still remove?" Ask via AskUserQuestion.
   - If **no PR found**: warn: "No PR found for <branch>. `git branch -D` will permanently delete any unpushed commits on this branch. Still remove?" Ask via AskUserQuestion.

5. **For each approved worktree**, remove it and track success:

   ```bash
   git worktree remove <path>
   ```

   If removal fails (dirty worktree), warn the user and ask via AskUserQuestion whether to force-remove. Track which worktrees were **successfully removed**. Only these branches are eligible for deletion in Step 6.

6. **Delete local feature branches only for successfully removed worktrees.** These are squash-merged so git won't recognize them as merged. Use `-D`:

   ```bash
   git branch -D <successfully-removed-branch1> <successfully-removed-branch2> ...
   ```

   Do NOT delete branches for worktrees that failed removal or where the user declined force-remove. This would orphan the worktree.

7. **Verify clean state:**

   ```bash
   git worktree list
   git branch -a
   ```

8. **Clean remaining gone branches** that are not associated with worktrees (e.g. branches created outside the worktree workflow). After worktree-specific cleanup is done, check for any additional local branches whose remote tracking branch is gone:

   ```bash
   git branch -vv | grep '\[gone\]'
   ```

   Delete each one individually (these are non-worktree branches, safe to delete without worktree removal):

   ```bash
   git branch -D <branch-name>
   ```

   This handles gone branches that `/post-merge-cleanup` and `/clean_gone` would otherwise catch; no need to run them separately after `/worktree cleanup`.

9. **Report summary:**

   Report: "Clean. Main up to date, N worktrees removed, N branches deleted, M gone branches pruned."

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

4. **Check dependency staleness** for each worktree. Compare lock files against main to detect if deps need re-syncing after a rebase:

   ```bash
   git -C <path> diff --quiet main -- uv.lock
   git -C <path> diff --quiet main -- web/package-lock.json
   git -C <path> diff --quiet main -- cli/go.sum
   ```

   Each command exits 0 if no difference, 1 if different. If any lock file differs from main and the worktree is behind, flag it as "deps stale" in the status table.

5. **Present a summary table** (show both ahead AND behind counts):

   ```text
   Worktree             | Branch                    | vs Main            | PR     | Status     | Deps
   wt-delegation        | feat/delegation-loop-prev | +5 ahead           | #142   | clean      | ok
   wt-parallel          | feat/parallel-execution   | +3 ahead -2 behind | --     | 2 modified | stale
   wt-memory            | feat/memory-layer         | up to date         | #155   | clean      | ok
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

   TIER 0 -- No dependencies (all prereqs closed)
     ✅ #8   Message bus                    [critical]  communication/
     ✅ #10  Agent-to-agent messaging       [critical]  communication/
     ⬜ #134 Plan-and-Execute loop          [medium]    engine/

   TIER 1 -- Depends on Tier 0
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

   If dirty, warn and skip immediately: "Worktree <name> has uncommitted changes. Skipping rebase."

3. **Then check ahead/behind status relative to main:**

   ```bash
   git -C <path> rev-list --left-right --count main...<branch>
   ```

   This outputs two tab-separated numbers: `<left>\t<right>` where left = commits on main not on branch (behind), right = commits on branch not on main (ahead).

   - If **0 behind AND 0 ahead**: fully up to date. Skip.
   - If **0 behind AND N ahead**: branch is ahead but main hasn't moved. Skip (rebase is a no-op, branch already contains everything from main).
   - If **M behind** (regardless of ahead count): this worktree needs rebasing. Warn the user: "Worktree <name> is M commits behind main (and N ahead). Rebase may cause conflicts." Ask via AskUserQuestion: rebase anyway / skip / abort all.

4. **For approved worktrees**, rebase:

   ```bash
   git -C <path> rebase main
   ```

   If rebase fails (conflicts), abort and report:
   ```bash
   git -C <path> rebase --abort
   ```

5. **Re-sync dependencies** for each successfully rebased worktree. Lock files (`uv.lock`, `package-lock.json`, `go.sum`) may have changed after rebase:

   ```bash
   uv sync --project <path>
   npm --prefix <path>/web ci --silent
   go -C <path>/cli mod download
   ```

   Run sequentially per worktree to avoid cache lock contention.

6. **Verify:**

   ```bash
   git -C <path> log --oneline -1
   ```

7. Report: "N worktrees rebased to <commit> and deps re-synced. M skipped (have local commits or dirty state)."

---

## Rules

- **Never force-remove** a worktree without asking the user first.
- **Never delete branches** without checking PR merge status first.
- **Always check `.claude/` local files exist** before copying; warn if missing.
- **Repo name detection**: extract from the repository's canonical root (`basename $(git rev-parse --show-toplevel)`), not the current directory basename. Strip any existing `wt-` prefix to avoid nested names when running from inside a linked worktree.
- **Owner/repo detection**: extract from `git remote get-url origin`.
- **Platform-aware paths**: derive worktree absolute paths dynamically at runtime. On Windows, convert to backslash paths for user-facing output. The `cd <path> && claude` instructions are for the user's own terminal, not Bash tool invocations.
- Worktree directories are always siblings of the main repo directory (`../`).
- When generating prompts, read the actual issue bodies. Do not guess or use stale information.
- Parse `spec:*` labels to auto-match source directories for prompt generation.
- Parse `## Dependencies` sections to auto-detect implementation order.
- **Input validation (CRITICAL):** Before interpolating any user-provided value into shell commands, validate:
  - Issue numbers: must match `^[0-9]+$`
  - Branch names: must match `^[a-zA-Z0-9/_.-]+$`
  - Label/filter values: must be a reasonable alphanumeric pattern (no shell metacharacters)
  - Owner/repo (from `git remote`): must match `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$`
  - Directory paths: must not contain shell metacharacters (`;`, `|`, `&`, `$`, `` ` ``, `(`, `)`)
  - Reject and warn if any value fails validation; do not execute the command.
- **Package manager cache lock contention:** When multiple worktrees run package manager commands concurrently, they can serialize on global cache locks, causing all instances to appear stuck. The `setup` and `rebase` commands pre-sync dependencies sequentially to avoid this. If instances hang:
  - **uv**: lock at `$LOCALAPPDATA/uv/cache/.lock` (Windows), `$HOME/.cache/uv/.lock` (Linux), `~/Library/Caches/uv/.lock` (macOS). Check: `tasklist | findstr uv` (Windows) or `ps aux | grep uv`. Remove stale lock if no processes running.
  - **npm**: lock at `$LOCALAPPDATA/npm-cache/_locks/` (Windows), `$HOME/.npm/_locks/` (Linux/macOS). Check: `tasklist | findstr npm` or `ps aux | grep npm`.
  - **Go**: module cache at `$GOPATH/pkg/mod/cache/lock` (or `$HOME/go/pkg/mod/cache/lock`). Rarely contends but possible under heavy parallel downloads.
- If `$ARGUMENTS` is empty or doesn't match a command, show a brief usage guide:

  ```text
  /worktree setup <definitions>             Create worktrees with prompts
  /worktree setup --issues #26,#30          Issue-aware setup
  /worktree setup --no-launch <defs>        Setup without opening WT tabs
  /worktree cleanup                         Remove worktrees after merge
  /worktree status                          Show worktree state
  /worktree tree --issues #26,#30           Dependency tree view
  /worktree rebase                          Update worktrees to latest main
  /worktree launch                          Open WT tabs for existing worktrees
  ```
