---
description: "Run one recursion-depth harness experiment as the experimenter, with the treatment verified on the wire before a single cell is paid for, and its row written to the harness round log when it stops"
argument-hint: "[--smoke-only] [--resume <out-dir>] [--depths <caps>]"
allowed-tools:
  - Bash
  - PowerShell
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# harness-experiment

Run one experiment through the recursion-depth harness and read the result
back from what was recorded, never from what was configured. This is the
**experimenter seat**: the treatment is the variable, and everything else is
held at what the product ships.

The sibling skill, [end-to-end-run](../end-to-end-run/SKILL.md), is the
**operator seat**: every ceiling at its shipped default, everything through the
dashboard, the product measured as a customer meets it. The two do not overlap.
An experiment that touches the dashboard is measuring the wrong thing, and an
operator run that changes a ceiling is no longer an operator run.

Design contract: [recursive-decomposition.md](../../../docs/design/recursive-decomposition.md).
Prior rounds: [harness-round-log.md](../../../docs/reference/harness-round-log.md),
which carries every recording under `evals/recursion_depth/results/`, what it
measured, why it stopped, and the engine wiring it ran under. Read it first and
add this round's row when it stops.

## What an experiment must prove

Two claims, in this order, and the second is not attempted until the first is
recorded.

1. **The treatment is on the wire.** Every treatment the manifest names (the
   reasoning depth, the tool surface, compaction, stagnation, memory, budget,
   prompt caching) is read back from the recorded request body and the emitted
   events of a one-cell smoke, and the report states each one beside its
   evidence. A 200 response, a valid config and a green unit test are all
   compatible with the feature being absent; eight recordings were built on an
   engine wired with 8 of 51 collaborators and nothing could tell.
2. **The curve is a curve.** Every depth cap carries the repetition floor, the
   headline metric is tokens per solved requirement with its bootstrap
   interval, and an interval that spans the neighbouring arm is reported as
   indistinguishable rather than ranked.

## Rules that do not bend

Each rule has an incident behind it. The incident is the reason.

- **Verify the treatment on the wire before paying for the cell.** Run
  `--smoke` first; `--record` refuses to start without a passing smoke for this
  manifest digest, and that refusal is the rule, not an obstacle to route
  around. Eight recordings measured an engine the product does not ship
  (no compaction, no review pipeline, no approval gate, no policy engine, no
  budget enforcer, no memory) and the root-cause analysis built on them
  withdrew its own verdict.
- **Never `uv run` mid-sweep.** `uv run` re-syncs the environment, and a sync
  while a sweep holds the interpreter replaces the packages under it. Use
  `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` directly for
  anything run while cells are in flight.
- **Forecast before spending.** `make recursion-depth` prints the matrix and
  the projected session count and spends nothing. The session figure is a
  FLOOR: the real count is a product of branching factors the manifest cannot
  predict, and a uniform branching model priced depth 4 twelve times too high.
  Read the forecast, read the ceiling, then record.
- **Resume, never restart.** `--resume` replays every MEASURED cell for free
  and attempts the unavailable ones again. A killed sweep restarted whole
  re-pays every cell it had already bought, and a killed sweep resumed from a
  changed commit is refused by the journal header, correctly: the journal
  identity pins the commit, the manifest, the spec and both pairs by name.
- **The repetition floor is five.** Below five draws every pairwise confidence
  interval in the published harness comparison crossed zero. The manifest
  refuses to load a lower count, and `--repetitions` refuses to set one. Stage
  the deep end with `--depths`, `--max-sessions` and `--resume`; do not lower
  the floor.
- **Read arms from `progress.jsonl` and `cost_usage.log`, never the transcript
  tap.** The tap corrupts about 8% of its lines under concurrency, so absence
  there never proves absence anywhere. The session journal is the spend ledger
  and the only one; a cell row is those same sessions added up again, so
  reading both double-counts.
- **Read what a tree CONTAINS.** A plan says what a unit was asked for, a
  session says what it claims to have done, and neither is delivery. Four
  separate recordings inferred delivery from a plan and were wrong four times:
  a leaf that wrote ten modules was recorded as having produced nothing, and a
  merge that assembled the whole package was recorded as changing nothing
  because it skipped its markdown report. Open the tree.
- **Bin errors by time and check NAMES, not counts.** A retry fixes a
  forgotten file; it never fixes a name eight children spelled eight ways. When
  a merge fails, list the identifiers each subtree exports and compare them,
  rather than counting how many attempts it took.
- **The committed manifest is the experimental design.** A treatment is a
  per-run flag (`--executor-reasoning-effort`, `--executor-temperature`,
  `--leaf-reasoning-effort`, `--repetitions`) folded into the provenance, never
  an edit to the file whose digest the journal pins. Two variants that differ
  by an edit cannot be told apart later, and an untracked variant marks the
  whole recording dirty.
- **Never run two sweeps against one provider window.** The flat-rate
  connections these sweeps run against meter a shared session window, and a
  second sweep does not add capacity, it halves the first one's.

## Procedure

### 1. Read the log and state the question

Open the round log. Write down, before anything runs, which arm this round
varies, what the previous round's stop was, and what would count as the stop
having moved. An experiment without a stated question produces a recording,
not a result.

### 2. Forecast

```bash
make recursion-depth
```

Prints the matrix, the repetition counts, the projected session floor and the
hard ceiling. If the ceiling is below the floor, the manifest is wrong; fix it
before spending. Confirm the sandbox image resolves: the recorded 404 on a
tag that did not exist cost two cells, because the preflight never asked.

### 3. Smoke

```bash
make recursion-depth-record ARGS="--smoke --company-config <yours> --sandbox-image <image>"
```

One cheap cap-1 cell. The wiring report lands at `<out-dir>/smoke/wiring.json`
and the run exits non-zero if any finding failed. Read every finding: a
finding that is `null` was not verified, not passed, and the recording will
say so beside the result. Do not proceed on an unverified treatment that the
question depends on.

### 4. Record

```bash
make recursion-depth-record ARGS="--company-config <yours> --sandbox-image <image> --depths 1,2"
```

Stage the deep end. Stages are cumulative: every later stage names every cap
recorded so far plus `--resume`, because the report holds exactly the caps the
invocation planned, and a journalled cell replays for free.

While it runs, read `progress.jsonl` beside `cells.jsonl` in the output
directory. Every session is journalled the moment it returns, with its tree on
the planning row, so a killed sweep has lost nothing but the session in
flight.

### 5. Read the result

The report is `depth_curve.md` and `chart.svg`. The headline is tokens per
solved requirement with its interval; spec satisfaction is the top panel and
the caption says which is which. Open the trees of the cells behind any number
that surprises you. A dead deliverable beside a high score is the published
failure mode and the report marks it as such in the "Deliverable" column.

### 6. Write the row

Add the round to the harness round log before doing anything about what it
found. Name the treatment, what the wiring report verified, how far it got,
why it stopped, and the engine wiring it ran under. A round whose row is not
written is a round that will be run again.
