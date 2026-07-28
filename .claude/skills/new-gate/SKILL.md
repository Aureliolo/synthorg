---
description: "Ship a convention gate: the enforcement script, its wiring, the inventory rows, its tests, and the docs the meta-gate checks"
argument-hint: "<gate-name> [the rule it enforces]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - AskUserQuestion
---

# Ship a convention gate

Convention Rollout is MANDATORY: every convention PR ships its enforcement gate.
The ritual spans six files and a meta-gate that fails the push if any of them is
missed, so it is worth doing mechanically rather than from memory.

Read [convention-gates.md](../../../docs/reference/convention-gates.md) first.
The registration procedure at the end of that page is the contract; this skill is
how to execute it without losing a step.

## Phase 1: Decide what the gate can actually decide

Before writing anything, answer: is the rule decidable from the AST of the files
in scope, without running the code?

A gate that needs runtime behaviour, type inference, or human judgement is not a
gate. Those become an `exempt` entry in `scripts/convention_gate_map.yaml` with a
real justification, and the rule is enforced by review or by a subagent instead.
The Design Spec and Planning rules are both exempt for exactly this reason: do
not invent a script that pretends to enforce a process rule.

Then decide the **opt-out shape**, because it determines the gate's structure:

| Shape | Use when |
| --- | --- |
| Per-line `# lint-allow: <name> -- <reason>` | The rule has genuine exceptions. **Default choice.** Reason is mandatory and non-empty. |
| Baseline file | An existing tree has violations a rollout has not reached. The file shrinks monotonically. |
| No opt-out at all | A suppression marker would defeat the rule's whole purpose (see the argument-count gate). |

If both a baseline and a per-line opt-out apply, take both: the baseline absorbs
history, the marker handles the future.

## Phase 2: Write the gate

Gates live at `scripts/check_<name>.py`. Reuse the shared helpers rather than
re-parsing by hand:

```python
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import GateSourceError, parse_source  # type: ignore[import-not-found]
else:
    from scripts._gate_source import GateSourceError, parse_source
```

`_gate_source` gives you `parse_source` / `read_and_parse`, plus
`reachable_statements` and `direct_body_nodes`, which matter more than they look:
a gate that walks unreachable code reports violations that cannot fire, and one
that walks nested bodies when it meant the direct body reports the opposite.

Requirements the tree already enforces on your gate:

- Exit codes: `0` clean, `1` violations, `2` an argv or config error. A gate that
  cannot trust its own scan must return `2`, never `0`. Silently passing is the
  one failure mode that makes a gate worse than absent.
- Output names the file, the line, and the opt-out. A violation the developer
  cannot act on from the message alone will be worked around, not fixed.
- Scope encoded in the gate, not the caller. If the rule covers `tests/` as well
  as `src/synthorg/`, walk both: a PR adding a violation in an unlisted tree
  would otherwise pass.
- `scripts/` is exempt from the ruff DOC rules but **not** from mypy strict.
- If the rule is decidable from a single file, add a `--files` flag so the gate
  can also run at agent time via `run_edit_time_gates.py`.

## Phase 3: Wire it so it runs locally and in CI

Two mutually exclusive options, per the registration procedure:

- **Push-only Python gate**: append the stem to the `_GATES` tuple in
  `scripts/run_prepush_python_gates.py`. The single `consolidated-python-gates`
  hook runs every entry across a bounded worker pool. Do **not** also add a
  per-gate pre-push hook; that is what the consolidation exists to avoid.
  Gate contract: it must be stateless with respect to its siblings, since they
  share reused workers.
- **Also needed at pre-commit**: give it its own `.pre-commit-config.yaml` entry
  with `stages: [pre-commit, pre-push]`.

`check_local_ci_parity.py` verifies the consolidated hook rather than individual
push-only gate ids, so a `_GATES` entry inherits CI coverage automatically.

## Phase 4: Inventory rows (the meta-gate checks these)

Three edits, all required:

1. `scripts/convention_gate_map.yaml`: an entry keyed to the MANDATORY paragraph,
   carrying either `gate: scripts/check_<name>.py` or `exempt: { reason: ... }`.
   `check_convention_gate_inventory.py` fails the push if a MANDATORY paragraph in
   the canonical doc set has neither.
2. `docs/reference/convention-gates.md`: a row in the gate-inventory table
   recording stages, scope, full-vs-changed, baseline-driven, and verdict.
3. The `<!--RS:convention_gates-->` count macro in that same page.

The macro counts `check_*.py` scripts. A helper named anything else (`run_*.py`,
`_*_lib.py`) is deliberately not counted and needs no map entry, which is the
right shape for a dispatcher or shared library rather than an enforcement gate.

If the gate ships a baseline, note that `check_baseline_growth.py` blocks adding
entries at commit time without `ALLOW_BASELINE_GROWTH=1`. That flag needs explicit
user approval; never set it unilaterally, and never edit a baseline file directly
(PreToolUse blocks it).

## Phase 5: Test it

Add `tests/unit/scripts/test_check_<name>.py`. Cover, at minimum:

- A clean fixture returns `0`.
- A violating fixture returns `1` and names the file and line.
- The opt-out suppresses, and a **bare** opt-out with no reason does not.
- A malformed or unreadable input returns `2`, not `0`.
- If baseline-driven: a stale entry is reported as drift rather than tolerated.

The last two are what stop a gate rotting into a no-op. Use `tests/unit/scripts/fixtures/`
for source fixtures rather than writing into the real tree.

## Phase 6: Verify

```bash
uv run python scripts/check_<name>.py
uv run python scripts/check_convention_gate_inventory.py
uv run python scripts/check_doc_numeric_macros.py
uv run python -m pytest tests/unit/scripts/test_check_<name>.py
```

Run the gate against the whole tree before wiring it in. A gate that fails on
existing code needs a baseline decision made deliberately in Phase 1, not
discovered at push time. Surface the count to the user and let them choose
between a baseline and fixing the violations; do not quietly baseline them.

## Definition of done

- [ ] Rule is genuinely statically decidable (or it is an `exempt` entry instead)
- [ ] Gate exits `2` when it cannot trust its own scan
- [ ] Wired to `_GATES` or to its own pre-commit entry, not both
- [ ] `convention_gate_map.yaml` entry present
- [ ] Inventory table row plus count-macro bump
- [ ] Tests cover clean, violating, opt-out, bare-opt-out, and unreadable input
- [ ] Whole-tree run is clean, or the baseline decision was the user's
