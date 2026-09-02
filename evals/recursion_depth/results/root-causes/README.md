# Root causes, not symptoms

Written 2026-09-01 after measuring the loop off the wire rather than off its
configuration. The verdict up front, because it is the thing worth arguing
with:

> **The LOOP is fundamentally flawed and should be replaced, not tuned. The
> HARNESS around it (recording, journalling, grading isolation) is sound and in
> one respect ahead of published practice.**

Those are different objects and the evidence separates them cleanly. What
follows is six root causes. Every symptom this project has recorded is one of
them wearing a different hat.

---

## RC1: the loop is open-loop per unit and closed-loop only once, at the end

A leaf gets **one session**. No review, no repair, no second opinion. `run_leaf`
re-runs only on infrastructure error, never on quality, and the code says so:
*"a leaf gets no repair round"*. The only review in the entire loop is on the
merged trunk, after every unit has been built.

So a unit that misread its requirement is discovered, if at all, after eight
units have built on the misreading, by an agent that was not there when it
happened.

This is precisely the failure mode the one published harness ablation was built
against, in their words: *"the agent wrote a solution, re-read its own code,
confirmed it looks ok, and stopped."* Their answer to it included per-unit
Build→Verify→Fix against the ORIGINAL SPEC rather than the agent's paraphrase.

**What that source does and does not license.** Holding the model fixed and
changing only the harness moved a coding agent 52.8% -> 66.5% on
Terminal-Bench 2.0, but that is the baseline-to-final figure across FIVE
combined changes, and the source says it does not isolate their individual
contributions. Only one of the five carries a published isolated ablation, and
it is the reasoning schedule rather than this. So the citation supports "a
harness without self-verification is the shape they set out to fix" and does
not support any number attached to fixing it here. The case for RC1 rests on
what the corpus shows, below, not on borrowing that 13.7 points.

**Not tunable.** No amount of budget, prompting or reviewer quality converts a
one-shot pipeline into a feedback loop.

## RC2: isolation is enforced exactly where agreement is required

Units are isolated **by construction**: each builds in a workspace recreated
from a seed, and none can see any sibling's tree. They must nonetheless agree on
every interface they share.

So the design manufactures the divergence it later pays a merge to reconcile.
The leaf brief said so in as many words before the contract stage existed: *"the
pieces around you are being built at the same time by others."*

Measured, shared modules that disagreed on their exports:

| cell | diverged |
|---|---|
| three corpus cells | 11/14, 11/12, 12/13 |
| with a contract stage | **0/21** |

The contract stage fixes the *symptom* well, and that it works is the proof that
isolation was the cause. What it does not change is the rule that a unit never
sees another unit's work, ever, which is what makes big-bang integration
mandatory rather than chosen.

## RC3: the merge is a job created by RC1 and RC2, and no agent can do it well

"Read eight trees, reconcile eight interpretations, emit one working program,"
with no record of how they diverged, no tests that arbitrate, and a context that
cannot hold eight trees.

Measured on merge sessions alone (reviewers excluded, which matters: a review
transcript is named after the merge it judges, and counting them together
flattered the merge):

| | share of merge tool calls |
|---|---|
| shell | **84%** |
| `write_file` + `edit_file` | 12% |
| `read_file` | 3% |

And within the shell, by leading program (past the `cd` that hides 62% of them):

| | merge | leaf |
|---|---|---|
| looking (`cat` `grep` `ls` `find` `wc` `head`) | **~50%** | 13% |
| patching by hand (`sed`) | 22% | 4% |
| actually running the code (`python`) | **15%** | 74% |

A leaf uses the shell to RUN things. A merge uses it to LOOK, one file at a
time, across every subtree. A live merge observed mid-run was 41 turns in with
**zero** test executions. Every merge in the corpus ran to 94-99% of its
ceiling; not one converged.

**Not tunable.** This stage exists to undo RC2. Fix RC1 and RC2 and there is
almost nothing left for it to do.

## RC4: nothing manages context

History is monotonic and re-sent whole. Nothing is evicted, summarised or
pinned. Measured: 178 messages on a leaf's 77th turn, **225 on a merge's
108th**; 95-100% of every session's emitted characters are hidden reasoning, at
turn granularity, worst case 99.9%.

Letta reached 4th on Terminal-Bench with Claude Sonnet 4, reporting it roughly
matched Claude Code on Opus 4, in under 200 lines, crediting one mechanism
class: a pinned read-only task block, a read-write todo block the agent edits
itself, and compaction of the message history at 40k tokens with the blocks
never compacted. The underlying MemGPT policy splits the decision correctly:
the SYSTEM decides when to evict, the MODEL decides what is worth preserving
first.

We have none of it.

## RC5: the harness validates CONFIGURATION, not CAPABILITY

The sharpest instance cost two cells today. Every sweep cell booted with a
sandbox image that does not exist:

```
DockerError: [404] No such image: ghcr.io/aureliolo/synthorg-sandbox:v0.9.3
```

The tag is untagged upstream; the working reference is a digest. The queue's
shared arguments never carried `--sandbox-image`, so each cell booted fine, ran
a 74-turn contract session, spent real money, and only failed at GRADING, with
`the sweep measured no cells`. There is a preflight and it did not ask whether
the image resolves.

The same root cause, wearing other hats, across this project's whole history:

| symptom | what was configured | what was true |
|---|---|---|
| sandbox image 404 | a tag in the manifest | the tag does not exist |
| sandbox healthcheck never passed | a HEALTHCHECK in the image | the keep-alive replaced the CMD that served it; 5 tests passed anyway |
| prompt caching "on" | a capability flag | LiteLLM has no entry for the model, so it defaulted False; 80.7M tokens uncached |
| `reasoning_effort` set | a field on the pair | stripped for a model the routing table does not know |
| `max` reasoning tier | a value an operator would write | the product's vocabulary cannot spell it; reachable only by omitting the field |
| lazy tool loading | a discovery protocol in the prompt | advisory; tools are called by name unadvertised |

Every one of these was ACCEPTED and then silently did nothing. A 200 response,
a parsed config and a passing unit test are all compatible with the feature
being absent.

**The fix is a discipline, not a patch**: nothing is believed until it is
observed on the wire or in the artefact. `report_session_flow.py --wire` is the
first instrument that does this for one parameter. There is no equivalent for
the rest.

## RC6: the instruments were built after the fact and are themselves unvalidated

Every measurement bug found today produced a *plausible* wrong number, which is
why none of them was caught by reading the output.

| instrument | bug | what it reported |
|---|---|---|
| session-kind filter | `--session merge` is a substring, and reviews are named `...-merge-<id>-review2` | reviewers' calls counted as the merge's |
| repeat detector | keyed on the tool NAME | ~half of all turns "looping"; keyed on arguments it is 5% |
| shell mutation counter | `>` matched `>/dev/null` and `WHERE qty > 1` | 13.6% mutating; corrected 8.3% |
| contract collectability | matched pytest strings the grader never emits | every contract classified fine |
| `missing_declared_paths` | field name inverted its meaning | a failed assembly read as "wrote a report, touched no code" |
| delivery gate under a contract | ran the whole suite | 0/3 units delivered against 6/6 in the control |

The pattern: a metric is written from what the author *expects* the source to
say, never checked against what it *does* say. That is RC5 pointed at the
measuring apparatus instead of the product.

---

## What is NOT broken, and should not be touched

**Grader isolation, which is ahead of published practice.** The most damaging
finding in the benchmark literature is that Terminal-Bench's default put the
verifier in the agent's own container: all three top Terminal-Bench-2
submissions cheated through it, one reading `/tests`, two injecting expected
answers into their own context file (stripping those dropped one from 81.8% to
71.7%, first place to fourteenth). Harbor's remedy is opt-in per task.

Ours is not opt-in. A throwaway container per grading, in a directory no agent
workspace is ever mounted from; the oracle deletes its own expectations once
collection has imported them and before any test body runs; what may remain is
an ALLOWLIST, because the denylist version left `__pycache__` holding the
expected rows in `co_consts`; and `PYTHONDONTWRITEBYTECODE` is set because
pytest's assertion rewriter would otherwise recompile them beside the graded
tree mid-run.

**The tool surface.** Five real tools. The published result about deleting
sixteen specialised tools describes a place we already stand, and its actual
lesson (stop pre-processing for a capable model) is not about tool count.

**Journalling.** Per-session rows, flushed and fsynced, resumable. The reason
any of today's analysis was possible at all.

---

## The change this implies

Not a list of fixes. One replacement:

```
contract  ->  per-unit build/verify/fix against the contract's own tests
          ->  each unit merges into the trunk WHEN IT LANDS
          ->  the final stage checks, it does not reconcile
```

That collapses RC1, RC2 and RC3 together, and it is what the product's own
`SKELETON` + git-worktree model already does. The plan's Phase 2d specified it;
the code still hands the merge `.children/*/`, so it was never built.

Then RC4 on top: a durable per-session plan block and compaction, so a 108-turn
merge is not re-sending 225 messages.

RC5 and RC6 are not features, they are the rule that nothing counts until it is
observed. Every arm from here needs its treatment verified on the wire before
the cell is paid for.

---

## What this dossier measured, and two corrections

RC5 applied to this dossier itself. Every recording it reads was built by the
harness's own engine construction, which passed 8 of the 51 collaborators the
product's boot path passes, and nothing could tell: omitting a keyword argument
looked exactly like deciding against it. So RC4 measured a harness that passed
no compaction callback, RC1 measured one that passed no review pipeline, and
the budget signal counted as delivered was the task's own token ceiling
standing in for an enforcer that was not there. The verdict above stands as a
description of what the corpus contained; whether the loop is flawed or the
harness was is what the next recording, the first built on the product's own
assembly, exists to answer.

Two figures in the parity analysis were wrong and are corrected here rather
than silently:

- **The stagnation detector was never a parity gap.** The product's own
  default strategy was `off`, so a default deployment built no detector
  either, and the harness omitting one matched the product exactly. That is
  why the default changed (to `tool_repetition`) rather than the harness: the
  reviewer that re-ran an identical probe twenty times went undetected in the
  product too.
- **The gap was 43 collaborators, not 56.** The analysis counted every one
  of the constructor's 64 keywords the harness did not pass. Eleven of those
  (`model_resolver`, `provider_configs`, `coordinator`, `execution_loop`,
  `shutdown_checker`, `tool_invocation_tracker`,
  `ontology_injection_strategy`, `capture_strategy`, `event_reader`,
  `approval_interrupt_timeout_seconds`, `checkpoint_config`) were not passed
  by the boot path either, and two more (`evolution_service`,
  `mcp_self_consumer`) are off by default, which leaves the 51 the product
  passes and the 43 of them the harness did not. After the harness switched
  to the product's assembly the gap is zero by construction, because a
  partial engine is no longer constructible.

## Re-measured through the product's engine (2026-09-02)

One cap-1 cell on the product's own assembly, nothing missing, with a wiring
smoke beside its journal (`../wired-r0/README.md` carries the register answer
by answer). The sweep was stopped at that cell by operator decision, so each
verdict below rests on one cell and says so.

- **RC1: refuted as the product's behaviour.** The post-execution path offers
  every finished unit for review, and the roster reviewer judged the assembly
  three times with code-quoted findings. What stands is that a unit which runs
  out of turns is never offered, and every leaf here did: the cap, not the
  loop, is what left the leaves unreviewed.
- **RC2: stands, smaller.** The contract stage closed most of the divergence
  (7 of 27 shared modules against 11 of 14 without a contract). What remained
  was one unit's join signature and one unit's error taxonomy.
- **RC3: stands, with the mechanism now on the wire.** The assembly was briefed
  with the reviewer's findings by name, read the named file four times, edited
  through `sed` and here-documents, and never wrote the two failing lines across 160
  further turns and 26M tokens. Repair does not converge because the assembly
  does not act on what it is told.
- **RC4: refuted as a cause at this pair.** The largest request was 125K
  tokens against an 838K compaction threshold; compaction correctly never
  fired, and tool-output abbreviation fired ten times. Nothing ran out of
  context.
- **RC5: resolved by construction**, and the smoke that replaced it read three
  of its own findings wrong on this cell (a spelling mismatch, a policy engine
  demanded where none is configured, an alias matched where the wire carries
  the routed id); all three corrected and tested.
- **RC6: partly stands.** The session-flow report read a non-streamed planning
  response as no calls at all; corrected. Every other instrument was re-run on
  the cell and agreed with the journal.

What the dossier could not see, and this cell did: the outcomes were decided
by a 40-turn cap with no extension and by a tool surface that refused, raced
or hid things an agent needed (a tokeniser refused as a secret, concurrent
edits of one file, missing parent directories, hidden parameter names, an
array sent as JSON text, a recordable test-command shape told only on
refusal). All fixed with tests on the branch carrying the recording. The
replacement this dossier proposed (incremental trunk integration) is neither
motivated nor refuted by one cell, and the sweep that would have decided it
was stopped; the direction question moved to #2916.
