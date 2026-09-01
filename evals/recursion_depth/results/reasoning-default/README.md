# The executor spent 99% of its output on hidden reasoning

Measured 2026-09-01 against the three depth-1 smoke cells already in
`results/`, plus a direct probe of the executor's endpoint. Nothing here was
re-recorded: the corpus transcripts already held the answer, and the probe cost
five completions.

## The finding

Across every session of all three recorded cells, between **95% and 100% of the
text the model emitted was `reasoning_content` rather than content or tool
calls.**

| session | reasoning chars | content chars | thinking |
|---|---:|---:|---:|
| merge attempt, smoke-c | 1,568,409 | 2,336 | 99.85% |
| merge attempt, smoke-b | 1,094,800 | 2,765 | 99.7% |
| leaf, smoke-c | 654,485 | 1,727 | 99.7% |
| leaf, smoke-a | 908,380 | 3,514 | 99.6% |
| planning session, smoke-c | 116,782 | 2,034 | 98% |
| the most favourable session in the corpus | 21,429 | 4,215 | 84% |

Characters off the streamed deltas, not billed tokens, so the two are not
converted into one another here. The ratio is what the record is for.

## Why

The executor is `glm-5.3-flash`. Its family controls thinking through
`reasoning_effort`, which takes `low`, `high` or `max`, and **defaults to `max`
when the field is absent**. In this family thinking cannot be switched off at
all.

The matrix left `reasoning_effort: null` for the executor, with a note saying
the value should be pinned from what the transcripts showed. That is what this
record does. Unset was never "no treatment": it was an unrecorded choice of the
most expensive tier available, on the pair that plans, builds every leaf and
assembles every node.

## The probe, and what it does NOT show

One identical open-ended prompt, an 8,192-token output cap, five
configurations. **The cap is load-bearing in reading this table**, and the
first version of this record overstated what it proves.

| sent | seconds | output tokens | reasoning chars | content chars |
|---|---:|---:|---:|---:|
| nothing | 120.6 | 8,192 (the cap) | 31,981 | **0** |
| `reasoning_effort=low` | 21.1 | 1,556 | 0 | 6,170 |
| `reasoning_effort=high` | 37.1 | 3,345 | 4,644 | 8,161 |
| `think=false` | 132.8 | 8,192 (the cap) | 34,088 | **0** |
| `chat_template_kwargs {thinking: false}` | 128.7 | 8,192 (the cap) | 34,066 | **0** |

Three things worth keeping.

**Unset returns nothing usable under a tight cap.** The thinking alone exceeds
8,192 tokens, so the reply is entirely reasoning and carries no content and no
tool call. Under the matrix's own 131,072 ceiling the model does reach content
eventually; it simply spends enormously first. A live contract session's first
turn spent 71,405 output tokens on 275,649 characters of reasoning and 305
characters of content, and took half an hour to say what it planned to do.

**The two obvious alternatives do not work.** `think: false` and
`chat_template_kwargs` were both accepted and ignored, reproducing the unset
row's numbers. That matches the vendor's own statement that 5.3 cannot disable
thinking, and it means `reasoning_effort` is the only lever there is.

**Acceptance proves nothing, which is why this was measured rather than
configured.** All five requests returned 200.

**And the tiers do not separate this way on the real workload.** Measured on
live planning sessions against this specification, `max` emitted about 81,000
characters of reasoning per exchange and `high` about 105,000. There is no
discount. What the probe caught is narrower than it looks: at `max` the
thinking alone exceeds a small cap, so a bounded reply carries nothing at all,
while at `high` it fits. That is a difference in whether a reply survives its
ceiling, not a saving per turn.

So nothing here supports "pinning `high` makes the sweep cheaper", and that
claim should not be made from this table. What it supports is narrower and
still worth having: the tier was never stated, it defaults to the most
expensive one, the two obvious ways to disable it do not work, and a reply at
`max` can consume its whole ceiling without acting. Which tier is actually
better on this workload is what `scripts/sweep_harness_variants.py` exists to
record.

## What it explains

Every one of these was recorded as its own finding before this one was
measured, and each is a symptom of it:

- 58% of leaves terminated on their token ceiling. They thought until the
  budget ran out.
- Every merge attempt in the corpus ran to 94-99% of its 5.5M ceiling. Not one
  converged; all were cut off, and what consumed them was thinking.
- The merges made 80-96% of their tool calls to the shell and 1-17% to a file
  writer. Almost no output budget survived to compose anything else. One merge
  made 223 shell calls and 7 file writes.
- Raising `unit_token_ceiling` to 3,000,000 made a cap-1 cell strictly worse
  (`results/ceiling-3m/`). More budget bought more thinking.

## What changed

`manifest.yaml` pins `reasoning_effort: high` on the executor. `high` rather
than `low` because the executor plans as well as builds, and it is the cheapest
setting measured that both reasons and reliably emits content.

That it reaches the wire is checked rather than assumed: the reviewer's
recorded request body already carried `reasoning_effort` while the executor's
carried no such key, and the driver declares the parameter allowed for a model
LiteLLM has no entry for, which is the only way it survives a custom endpoint
(`providers/drivers/litellm_features.py`, `RouteReasoningSupport.UNKNOWN`).

## What is NOT claimed

**That `max` reasons worse.** Nothing measured here scored anything. The
corpus ran at `max` and produced this project's two best recordings (39 and 40
of 42), and the vendor's own guidance is to keep `max` for benchmark
reproduction. Both of those point the other way, and neither is disputed.

The case against it is not about the quality of a turn's thinking, it is about
what an AGENTIC loop is measured on: work completed per budget, across many
turns, each of which has to end in a tool call. There the evidence is direct.
Every merge attempt in the corpus was cut off at 94-99% of its ceiling, so not
one of them finished, whatever the quality of the thinking that consumed it.
Raising `unit_token_ceiling` to 3,000,000 made a cap-1 cell strictly worse
rather than better. And under a tight output cap the mode degenerates
completely: a reply that is entirely reasoning emits no tool call, which is not
lower-quality work but no work.

The vendor's advice fits a single-shot leaderboard answer, where more thinking
is strictly better because nothing has to fit alongside it. It does not
transfer to a turn that must leave room to act.

`high` rather than `low` for the same reason: `high` still reasons (4,644
characters on the probe) AND answers (8,161), where `low` reasoned not at all.

**What the scores do at `high` is untested.** That needs cells recorded under
it, and a comparison is only honest between cells recorded under the same
setting. Two of the three cells running when this was written are at `max` and
one at `high` with an otherwise identical loop, which is the pair that answers
it; if `high` scores worse, that is the finding and this pin should be
revisited.

Nor does this retract any earlier finding. The interface divergence, the
absent contract stage and the big-bang merge are real and were measured on
their own evidence. What this changes is the account of the BUDGET: the loop
was not merely spending badly, it was spending almost all of it before it
could act.
