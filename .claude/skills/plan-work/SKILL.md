---
description: "Plan max-safe parallel worktrees: clean up, survey open issues (excluding renovate/backlog/maybe), map what's inflight, analyse dependencies + file conflicts, and propose the largest conflict-free set of worktrees that each fully close at least one issue. Hands groupings to /worktree setup."
argument-hint: "[--no-cleanup] [--issues #a,#b,...] [--no-bundle]"
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - AskUserQuestion
  - Task
---

# Plan Work

Answer, in one pass, the questions we ask every time we pick up work:

> What's open? What's inflight, and what files does it touch? What can we start right now, in parallel, without conflicts, where each worktree fully closes at least one issue, and what's the maximum number we can safely run?

This skill is planning plus orchestration. It does not create worktrees itself: it produces the conflict-free grouping and hands it to `/worktree setup`. It cleans up first so the "inflight" picture is true, surveys open work the way we always want it (no renovate, no backlog, no priority-sorting), builds the dependency and file-conflict graph against what's already inflight, and proposes the largest safe parallel set under the hard rules below.

**Arguments:** "$ARGUMENTS"

- `--no-cleanup`: skip Step 0 (use when the repo is already clean, or mid-flight worktrees must be preserved exactly).
- `--issues #a,#b,...`: restrict the candidate pool to these issues instead of the full open set.
- `--no-bundle`: never bundle a second issue into a small worktree (Step 5); one issue per worktree.

---

## Optimisation objective: least TOTAL work (criticality is NEVER a factor)

The goal is to minimise **total work across the whole backlog**, not to maximise parallelism for its own sake, and never to chase "important" issues first. Criticality / `prio:*` / "how urgent it looks" is NEVER an input to what we start or in what order. Ignore it completely.

What we actually optimise: the sequence with the **smallest sum of touched files / redone work** to clear the backlog. That changes ordering whenever issues overlap:

- **Rework-first.** If one issue rewrites, renames, moves, or deletes code that other open issues also touch or depend on, schedule that rework FIRST and let the others land on the finished surface. Doing the others first means redoing them (or resolving conflicts) after the rework lands, which is strictly more total work. A high-overlap foundational change is worth blocking a whole wave for, because it makes every downstream issue edit each file once instead of twice.
- **Don't parallelise work that will be reworked.** A candidate that is file-disjoint today but sits on a surface a pending rework will churn is NOT free to start: starting it just buys a future rebase/redo. Treat "will be reworked by an inflight or queued foundational change" as a **soft dependency** and hold it, even though the conflict scan says "safe".
- **Least-total-work can beat most-worktrees-now.** Sometimes the right plan is "land one foundational rework alone, then fan out wide" rather than "start five overlapping things now and merge-resolve forever." Prefer the smaller total even if it means fewer worktrees this instant.

This is exactly why a program that sequences a sweeping rework (an identifier or enum migration that touches every package, say) ahead of per-package polish is correct: the rework touches what the polish touches, so rework-first means each package is edited once, not twice. When you report the plan, justify the ordering by total-work, never by criticality.

---

## Hard rules (INVARIANTS, never violate)

1. **Every proposed worktree must FULLY close at least one issue.** No exploratory, "groundwork", or partial worktrees. If a candidate can't be driven to an issue's full acceptance criteria, it is not proposed. (Epic-validation worktrees satisfy this by closing the epic; see Step 6.)
2. **Size: 300 files is the only HARD cap; ~50-250 files / ~3-15k LOC is the WISHED target.** The single hard limit is **300 files** touched per worktree (estimate the footprint in Step 4); a single issue estimated above 300 files is flagged "must be split first" and never proposed as one worktree. There is **NO hard LOC cap**. Within the file ceiling, AIM for the wished sweet spot of about **50 to 250 files and about 3k to 15k LOC** per worktree, the reviewable range. These are targets, not gates: a worktree may land below 50 files when nothing adjacent fits cleanly (Rule 3), and LOC may run past 15k when one issue genuinely needs it. Decomposition work inflates file count ~3-4x (original modules + new split modules + import-site updates), so roughly 15-25 oversized source files per worktree lands in the target band.
3. **Bundle toward the target; never over-fragment.** Do NOT propose absurdly small single-concern worktrees (one per package, "decompose settings (2 files)", and the like). When carving an epic into worktrees, BUNDLE file-disjoint, unblocked, conflict-free, sensibly-adjacent issues/packages until each bundle reaches the ~50-250 file target, and let that one worktree close the several sub-issues it covers (multiple `Closes #N`). Bundling small fragments up to the target is the planning DEFAULT, not an afterthought; adding a *second whole issue* to an already-target-sized worktree stays an offered option (Step 5). It is still fine to leave a worktree single when nothing adjacent fits cleanly. Never exceed the 300-file hard cap when bundling. `--no-bundle` suppresses the optional second-issue bundle (one issue per worktree), but does not license proposing absurdly small fragments.
4. **Conflict-free.** A proposed worktree's file/package footprint must be disjoint from (a) every inflight worktree and (b) every other proposed worktree in the same wave. Any shared `src/`, `web/`, or `cli/` file is a conflict (be conservative; shared generated `data/*.json` also counts).
5. **Respect declared dependencies.** Only the head of a sequential chain is startable. A candidate is "unblocked" only when every `#N` in its `## Dependencies` section is closed, or is an inflight worktree whose landing-first is *established* by the Step 3a order-of-trust rule (a declared chain, or a green-and-approved PR), never merely assumed.
6. **Exclusions (standing).** Drop `renovate`, `label:backlog`, `label:maybe`, `label:maybe-future`, and Renovate/dependency-update PRs from the candidate pool unless the user explicitly asks for them. Epics/trackers are NOT startable issues: they're used only for grouping and epic-completion detection (Step 6).
7. **Criticality is NEVER a factor.** Never group, sort, lead, recommend, or sequence by `prio:*` or any notion of "importance" or "urgency". Ignore it completely. Order purely by least total work (see Optimisation objective) plus what's unblocked and parallelisable. Group by theme, epic, or dependency.

---

## Pipeline

### Step 0: Cleanup (unless `--no-cleanup`)

Make "inflight" mean genuinely unmerged work, not merged leftovers.

```bash
git checkout main && git pull
git fetch --prune
```

Find branches whose upstream is gone, and any worktrees attached to them:

```bash
git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads | grep '\[gone\]$'
git worktree list
```

For each gone branch with an attached worktree, remove the worktree first (`git worktree remove <path>`), then `git branch -D <branch>` it (explicit names, never piped/xargs bulk delete). Gone branches with no worktree: `git branch -D` directly. This is the `/post-merge-cleanup` plus `/worktree cleanup` core; do not also run those skills afterwards.

If a worktree is dirty (uncommitted work) its branch is inflight, not cleanup: leave it. Confirm clean:

```bash
git status
git worktree list
```

### Step 1: Survey open issues

Owner/repo from `git remote get-url origin`. Pull the actionable open set, applying the standing exclusions (Rule 6):

```bash
gh issue list --repo <owner/repo> --state open --limit 100 --json number,title,labels \
  --jq '.[] | select((.labels | map(.name) | any(. == "backlog" or . == "maybe" or . == "maybe-future")) | not) | select(.title | test("renovate|Update .* dependencies"; "i") | not) | "#\(.number)\t\(.title)\t[\(.labels | map(.name) | join(","))]"'
```

Separate the pool into:
- **Startable candidates**: concrete scoped issues with acceptance criteria.
- **Epics / trackers**: `type:epic`, or issues whose body is a checklist of child `#N`s. Park these for Step 6; they are never startable worktrees themselves.

If `--issues` was passed, intersect the candidate pool with that list.

### Step 2: Map what's inflight

For every non-main worktree (these survived Step 0, so each holds real work):

```bash
git worktree list --porcelain
```

For each `(path, branch)`, compute its **footprint** (committed diff plus uncommitted changes) and the issue(s) it's closing:

```bash
git -C <path> diff --name-only main...HEAD
git -C <path> status --short
git -C <path> log --oneline main..HEAD
```

Reduce each footprint to a **package set** (the top-level buckets: `src/synthorg/<pkg>`, `web/`, `cli/`, `tests/unit/<pkg>`, `data/*.json`, `docs/`). Record, per inflight worktree: branch, issue(s), file count, package set, and whether it's committed or still uncommitted. This package set is the conflict mask for Step 4.

Match each inflight branch to its issue(s) by `Closes #N` in the latest commit/PR body, or by branch slug versus issue title.

### Step 3: Dependency graph plus per-issue footprint estimate

For each startable candidate, fetch the body once:

```bash
gh issue view <N> --repo <owner/repo> --json number,title,labels,body
```

**a. Dependencies.** Parse the `## Dependencies` section for `#N` references and chain language ("After #X", "Blocks #Y", "Sequential after", "Parallel with siblings"). Resolve each `#N`'s state (`gh issue view <N> --json state`). A candidate is **unblocked** iff every dependency is closed, or is an inflight worktree whose landing before this one is *established*, never merely assumed.

Establish the landing order of a still-open inflight dependency in this order of trust: (1) a `## Dependencies` link makes this candidate explicitly "After #N", or the dependency `#N` declares the reverse chain; (2) the dependency's worktree already has an open PR that is green and approved, so it lands imminently. If neither holds, treat the inflight dependency as **blocking**: do NOT assume two concurrently-inflight worktrees land in any particular order. The conservative default is correct because a wrong "it lands first" buys a mid-flight rebase, the exact rework we are trying to avoid. When (1) or (2) unblocks a candidate, state the established order explicitly in the plan ("starts on #N, which lands first: declared chain / PR green and approved").

**b. Footprint estimate.** Build the candidate's package set from, in order of trust:
1. Explicit file/dir lists in the issue's `## Scope` (most issues here list them).
2. `spec:*` labels mapped to source directories (mapping below).
3. A glob count of those directories for the 300-file check: `git ls-files src/synthorg/<pkg> tests/unit/<pkg> | wc -l` for in-package work, or count the top-level bucket directly for `web/` or `cli/` (`git ls-files web | wc -l`, `git ls-files cli | wc -l`). Never prefix the top-level buckets with `src/synthorg/`; those paths hold no files. Bare; never pipe git through head/tail.

**`spec:*` to directory map: use the canonical table in the `worktree` skill (Step 6c).** It is the single source of truth; do NOT duplicate it here (a local copy drifted from the canonical one once already). Read that table for the label-to-directory mapping. It is a coarse fallback only: an issue's explicit `## Scope` file list (trust level 1 above) always beats it.

Record per candidate: unblocked?, package set, estimated file count, the issue(s) it fully closes.

### Step 4: Compute max safe parallelism

Start from the **unblocked** candidates only (Rule 5). Then:

1. **Hold soft-dependents (rework-first).** First identify the **foundational reworks** in the pool. Treat an open issue or inflight worktree as a foundational rework when any of these hold: (a) it carries a label signalling sweeping change (`type:refactor`, `type:tech-debt`, `scope:architecture`, `spec:architecture`); (b) its title or body uses migration language ("migration", "rename", "move", "dissolve", "consolidate", "everywhere", "project-wide", "all packages"); or (c) its estimated footprint (Step 3b) spans more than 3 packages or more than 100 files. Then drop any candidate that touches, or builds on, a surface one of those reworks will churn, even if it is file-disjoint today. Starting it just buys a future redo/rebase, which is more total work, not less. Hold it until the rework lands. (See the Optimisation objective.)
2. **Drop anything conflicting with inflight** (Rule 4): if a candidate's package set intersects ANY inflight worktree's package set, it's not startable this wave. Say so, and name the blocking inflight branch.
3. **Greedy maximal disjoint set** among survivors: order the queue with any foundational reworks (detected per Step 4.1) first, then the remaining candidates by footprint ascending; add a candidate to the wave iff its package set is disjoint from every already-selected candidate. Repeat until none fit. Seeding the reworks first keeps the rework-first objective intact when survivors do conflict (a large rework must not lose its slot to several small candidates sorted ahead of it); over a low-conflict, package-partitioned pool the pass still yields the maximum (the per-package hardening slices are disjoint by construction, so greedy is optimal there).
4. **Enforce 300-file cap** (Rule 2): any single candidate estimated above 300 is set aside as "split first", not proposed.

The size of the resulting set is the **max safe parallelism right now**. Also compute the **post-unlock** number: if a sequential chain or an inflight worktree is the only blocker, state what lands first and how many worktrees fan out immediately after (for example, "1 now; 11 once #2245 lands").

### Step 5: Bundling pass (unless `--no-bundle`)

For each selected worktree whose estimate is small (about 20 to 50 files), look for ONE more unblocked candidate that is (a) disjoint from every selected worktree's package set, (b) not already selected, and (c) sensibly adjacent (same epic, neighbouring package, or shares the read-context). If found and the combined estimate stays at or under 300 (target about 200), offer it as a bundle. Bundling is always an **option** surfaced to the user, never auto-applied, and it's fine to skip when nothing fits cleanly.

### Step 6: Epic-completion validation worktrees

For each epic/tracker parked in Step 1, resolve its child issues (the `#N`s in its checklist, or linked via `Tracked by`). If **all children are closed**, or all are closed once the current inflight lands, the epic is a candidate for a **validation worktree**:

> A worktree whose job is to confirm the epic is truly done end-to-end: re-verify every child's acceptance criteria against `main`, hunt for anything lost or regressed in the merges, check the epic's own gate/ratchet actually flipped, and surface improvements to land on top. Its deliverable closes the epic (satisfying Rule 1).

Propose it with branch type `chore/` or `refactor/` and the epic number as the issue it closes. Apply the same conflict rules: a validation worktree that only reads plus adds tests/docs usually conflicts with nothing and can run alongside the wave. Use the `issue-resolution-verifier` agent (model `sonnet`) inside that worktree to grade each child against the diff.

### Step 7: Present the plan, then hand to `/worktree setup`

Print, in this order:

1. **Cleanup result**: one line (main up to date, N worktrees/branches removed).
2. **Open**: startable candidates grouped by epic/theme (NOT by priority), with unblocked/blocked-by annotations. Epics listed separately with their done/total child count.
3. **Inflight**: table of worktree, branch, issue(s), files, package set, committed/uncommitted.
4. **Proposed wave**: table of worktree slug, issue(s) FULLY closed, est. files, package set, and bundle option (if any). Plus the headline: **"Max safe parallel right now: N. After <blocker> lands: M."**
5. **Blocked**: candidates held back, each with the one-line reason (sequential chain head not landed / conflicts with inflight branch X / above 300 files, split-first).

Then use **AskUserQuestion** for the path-forward decision (never free text). Make the full max-safe set the first option, labelled **(Recommended)**. Typical options: launch the full recommended set / launch a subset / apply a bundle / adjust grouping. Mark "best regardless of effort" as Recommended per standing preference.

On confirmation, create **all** chosen worktrees at once by invoking `/worktree setup` with the groupings (one line per worktree: `<branch> #issues "Description"`), so each gets the standard prompt plus dependency parsing plus dep-sync. Do not re-implement worktree creation here.

---

## Rules / notes

- **Conservative conflict model.** When unsure whether two issues share a file, treat them as conflicting. A false "conflict" costs one wave of latency; a false "safe" costs a manual merge-conflict resolution mid-flight. Prefer the cheaper error.
- **Inflight uncommitted work is still inflight.** A worktree with only `git status --short` changes (no commits), such as a large in-progress refactor, owns its whole footprint for conflict purposes. Do not propose anything overlapping it.
- **Never propose a worktree without a closing issue** (Rule 1). If the user wants ad-hoc/exploratory work, that's `/worktree setup "<description>"` directly, out of scope here.
- **Don't blame conflicts on "the program design".** When the project deliberately sequences waves (for example, enum dissolution then per-package hardening), report the unlock path concretely (which issue lands, how many fan out after), not "it's serial by design".
- **gh via Bash, never the MCP `list_issues`** (standing preference). Bare `git`/`gh`/`uv`, never piped through `head`/`tail` (truncation hides gate output), never `> file` redirects from Bash (blocked by hook; stream `2>&1` is fine).
- **Model pinning.** Any sub-agent spawned here (for example, footprint estimation across many issues, or epic validation grading) MUST pass an explicit `model`: `haiku` for mechanical counts, `sonnet` for dependency/acceptance reasoning. Never inherit.
- **Output discipline.** Tables over prose. Group by theme/epic. State the single max number prominently. The user asked a planning question: answer it decisively, don't survey.
