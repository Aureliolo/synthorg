# ADR-0011: Architectural feedback loop

## Status

Accepted. Implemented in EPIC #2046 PR 4 (issue #2050), completing the
"defence in depth" goal of ADR-0006.

## Context

ADR-0006 gates per-*file* badness: LOC against a tiered cap, plus ruff's
function-level complexity, public-method, statement, branch, and return
limits. Those catch a file that is too big or a function that is too hairy.
They are blind to *graph-level* smells that emerge across files:

- **Coupling hubs** -- a module imported by dozens of others becomes a
  change-amplifier and a merge-conflict magnet, regardless of its own size.
- **Cohesion decay** -- a large service class whose methods stop sharing
  state is several responsibilities wearing one class's coat; LOC alone does
  not see it.
- **Budget creep** -- a file quietly growing toward its tier cap gives no
  signal until the day it trips the gate, by which point the split is urgent
  rather than planned.

EPIC #2046's "every shape of architectural badness has at least one gate"
goal therefore needs a feedback loop over the whole graph, not just
per-file ceilings.

## Decision

### A committed metrics report

`scripts/architecture_report.py` computes, over `src/synthorg/`:

- **fan-in** -- the direct-importer count for each module.
- **LCOM4** -- a lack-of-cohesion measure for every service class of
  >= 400 LOC.
- **budget pressure** -- source files within 20% of their tier cap.

It writes `data/architecture_report.json`. That file is a **committed
baseline**, regenerated on demand (and in CI), exactly like the generated
feature index: the gate that consumes it never writes it.

### A drift gate

`scripts/check_architecture_drift.py` recomputes the same metrics live and
compares them to the committed baseline, failing on a regression past
threshold with structured remediation guidance. A regression is:

- **fan-in** -- a module's direct-importer count is at or above
  `FAN_IN_FAIL_THRESHOLD` (30) AND exceeds its recorded baseline by more than
  `FAN_IN_DRIFT_TOLERANCE` (a new hub, or a hub coupling materially harder
  than recorded).
- **budget pressure** -- a source file *newly* enters the within-20%-of-cap
  zone (a new file already close to needing a split).
- **LCOM4** -- a large service class becomes less cohesive than recorded, or
  a new >= 400-LOC service class lands with LCOM4 >= 2.

The shared computation lives in `scripts/_architecture_lib.py` so the report
generator and the gate cannot drift apart.

The gate deliberately does NOT write the report on pre-push: a hook that
dirties the working tree would trip the "files modified by hook" check and
fight the developer. The baseline is refreshed by running
`scripts/architecture_report.py` and committing the result.

### Enforcement

`uv run python scripts/check_architecture_drift.py` runs as a pre-push hook
and as a CI step; both `scripts/architecture_report.py` and the gate carry
`tests/unit/scripts/` coverage.

## Consequences

- A module that becomes a coupling hub, or a service class that loses
  cohesion, fails at push with a message pointing at the fix (invert onto a
  protocol; split the module; extract the incohesive responsibility) rather
  than going unnoticed until it is a god-module.
- The thresholds (30 fan-in, 20% budget zone, LCOM4 >= 2) are tunable in
  `_architecture_lib.py`; raising the bar later is a one-line change plus a
  baseline regeneration.
- The committed `data/architecture_report.json` doubles as a queryable
  snapshot of the codebase's coupling and cohesion profile for AI agents and
  reviewers, complementing the AI-navigation index (ADR-0010).
- The report is regenerated deliberately, not automatically; a legitimate
  increase in fan-in (a genuinely shared new protocol) is accepted by
  committing the refreshed baseline, which records the decision.
