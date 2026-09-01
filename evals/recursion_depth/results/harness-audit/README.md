# This harness measured against the published evidence

Four teams have published results in the last year on the scaffolding around a
model rather than the model. This audits our loop against each, using the flow
measurements in `../loop-flow/` rather than what our configuration claims.

Every number below is quoted with its source class. Where a technique is
credited but never isolated, that is said, because "part of a combined 13.7
point gain" is not evidence for the individual piece.

## The scoreboard

| Technique | Source | We | Evidence |
|---|---|---|---|
| Per-phase reasoning budget | LangChain, **the only real ablation** | **NO** | one global tier |
| Context compaction, durable plan block | Letta / MemGPT | **NO** | 225 messages, none evicted |
| Pre-completion verification gate | LangChain | **NO** | ours is post-session |
| Environment onboarding at start | LangChain | **NO** | 36% of contract-leaf calls are reads |
| Repetitions at the recommended floor | Terminal-Bench | **NO** | 2 per arm, floor is 5 |
| Spec-referential verification | LangChain | YES | the contract stage |
| "Graded by tests you never see" framing | LangChain | YES | `_ANTI_EXPLOIT` |
| Small tool surface | Vercel / Anthropic | YES | 5 real tools |
| Budget signal delivered to the agent | LangChain | YES | measured firing and consumed |
| Grade behaviour, not the reference's shape | Terminal-Bench | YES | declared paths never a verdict |
| Budgets sized to real paths, not the oracle | Terminal-Bench | YES | ceiling scales with claims |
| Grader isolated from the agent | Terminal-Bench | **AHEAD** | see below |
| Loop detection | LangChain | N/A | measured 5% repeats |

## Where we are ahead: the grader

The single most damaging finding in the benchmark literature is that
Terminal-Bench's default put the verifier in the SAME CONTAINER as the agent,
with tests copied into `/tests` at run time. All three top Terminal-Bench-2
submissions cheated through it: one read `/tests` directly, two injected
expected answers into their own `AGENTS.md` (stripping those dropped one from
81.8% to 71.7%, first place to fourteenth). Harbor's fix is an isolated
verifier environment that is **opt-in per task**.

Ours is not opt-in. `grading.py` stages the oracle and the graded tree in a
throwaway container built per grading, in a scratch directory no agent
workspace is ever mounted from, and:

- the oracle deletes its own expectations once collection has imported them,
  BEFORE any test body runs and therefore before the delivered program is ever
  spawned;
- what may remain is an **allowlist**, not a denylist, and the comment records
  why: the denylist version swept `test_*.py` and left `__pycache__`, whose
  compiled modules carried the queries and expected rows in `co_consts`;
- `PYTHONDONTWRITEBYTECODE` is set specifically because pytest's assertion
  rewriter gates its cache write on `sys.dont_write_bytecode`, so without it the
  expectations get recompiled beside the graded tree DURING the run, after the
  sweep that removed them;
- `oracle_fingerprint` / `oracle_leftovers` enforce it rather than trusting the
  construction.

The incentive analysis is also already written down: forging the unit's OWN
suite adds that unit's claims to the survival denominator and nothing to the
numerator, so forging drives the measured result down. There is no gradient
toward it.

Nothing to do here. Recorded because it is the one place the published
evidence says most projects are exposed and we are not.

## Gap 1: per-phase reasoning, and our sweep is on the wrong axis

This is the highest-value finding of the whole review, because it is the ONLY
technique in the LangChain post with a published isolated ablation, and it says
the axis we are currently paying to measure is the wrong one.

Holding GPT-5.2-Codex fixed on Terminal-Bench 2.0:

| reasoning schedule | score |
|---|---:|
| xhigh everywhere | 53.9% |
| high everywhere | 63.6% |
| **xhigh plan -> high build -> xhigh verify** | **66.5%** |

Two things follow. Maximal reasoning everywhere is WORSE than uniform high
(agents blew their time budget), which independently predicts our own measured
"raising `unit_token_ceiling` to 3M made a cap-1 cell strictly worse". And the
sandwich beats both uniform settings, so the win is in the SCHEDULE, not the
level.

Our sweep's reasoning axis is `default` / `high` / `low`, applied globally to
the executor pair. That is precisely LangChain's "all-xhigh vs all-high"
comparison: the two arms they published as the ones that lose. Whatever the
sweep returns, it cannot find the setting that won.

We already have the phase boundaries as first-class objects: `Role.CONTRACT`,
`Role.LEAF`, the merge and the review each resolve their own limits through
`session_limits_for(manifest, role, ...)`. Reasoning effort is currently the
one dial that does NOT vary by role.

**Done**, and verified on the wire before anything was paid for. Units now
build on a SECOND POOL of agents bound at their own depth rather than on a
re-pointed binding, because an agent is a fixed pair and work needing a
different one goes to a different agent. `sweep-sandwich-contract` is the arm.

The wire check mattered and is now permanent (`report_session_flow.py --wire`).
This stack strips `reasoning_effort` for a model its routing table has no entry
for, so a schedule that never reaches the provider would produce a cell
identical to its control and read as the treatment doing nothing. Measured
across every recording that still has its transcripts:

| recording | what each phase sent |
|---|---|
| pinned `high` | `contract=high`, `plan=high` |
| asked to omit | `contract=ABSENT`, `plan=ABSENT`, `leaf=ABSENT` |
| every recording | `review=high` |

So the parameter does reach the provider from an executor session once the
manifest pins one. `ABSENT` is a request rather than a gap on this family: an
omitted field runs at the vendor default, which is its most expensive tier.

Caveat that transfers with the technique: LangChain state plainly that their
harness gains did not carry to a different model untuned, so the ORDERING is a
hypothesis to test here, not a result to import.

## Gap 2: no context management at all

Letta reached 42.5% on Terminal-Bench (4th overall, 2nd among Sonnet-4 agents)
with Claude Sonnet 4, and reported it roughly matching Claude Code on Opus 4,
in under 200 lines. They credit one mechanism class, quoted: the agent's
"ability to manage its memory (the message history and memory blocks) allows it
to avoid common pitfalls like derailment and distraction".

What they run per turn:

- a **read-only** `task description` block, 5,000 tokens, holding the
  instructions verbatim;
- a **read-write** `todo list` block, 5,000 tokens, which the agent edits
  itself as it makes progress;
- the rolling message history, **compacted by recursive summarisation once it
  passes 40,000 tokens**. The two blocks are pinned and never compacted, which
  is the point: the plan survives the eviction of the history.

We do none of it. Our history grows monotonically and is re-sent whole: 178
messages on a leaf's 77th turn, 225 on a merge's 108th. There is no plan that
survives, no summarisation, no threshold. A merge rereads its whole history
every turn while foraging through subtrees one `cat` at a time.

The underlying MemGPT policy is worth copying exactly, because it splits the
decision correctly:

- at ~70% of budget, a warning is injected and **the model** decides what is
  worth writing into durable memory;
- at 100%, **the system** unilaterally evicts about half the queue into a
  recursive summary. Evicted messages stay retrievable.

Who decides IF to evict is the system; who decides WHAT to preserve is the
model. That split is the implementable takeaway.

**Action**: this is the largest single gap and the one that best explains our
cost curve. It is also the biggest change, so it goes behind its own arm.

## Gap 3: the verification gate is post-session, not pre-completion

LangChain's `PreCompletionChecklistMiddleware` intercepts the agent as it tries
to FINISH and forces a verification pass against the task spec before the turn
may end. The failure mode they name it against, quoted: the agent "wrote a
solution, re-read its own code, confirmed it looks ok, and stopped".

We have the harder half of this already, and arrived at it independently: the
contract stage makes verification spec-referential rather than self-referential
by writing one test per requirement from the specification, so a leaf's
definition of done is not its own opinion. That is the substance of the
technique.

What we do not have is the GATE. Our checks run after the session ends, so an
agent that stops early has already stopped. LangChain's runs while there is
still budget to act on the answer.

**Action**: worth an arm, cheaply. The pieces exist; what is missing is a
pre-exit hook.

## Gap 4: nothing maps the environment at session start

`LocalContextMiddleware` runs at agent start, maps the working directory and
its neighbours, probes for available tooling, and injects the result rather
than letting the agent discover it turn by turn.

Our contract-arm leaves spend **36% of their tool calls on `read_file`** and a
third of their shell calls on `cat`. Some of that is reading the agreement,
which is the intended behaviour and the mechanism the contract exists for. Some
of it is finding out what is there at all, which a start-of-session map would
answer once instead of over a dozen turns.

The two are not currently separable in the data. **Action**: separate them
first (is the read hitting `CONTRACT.md` and the skeleton, or is it `ls`-shaped
orientation?), then decide. Cheap to measure, and measuring it is the
prerequisite for knowing whether the fix is worth an arm.

## Gap 5: our repetitions are below the floor

Terminal-Bench requires a minimum of **5 trials per task**. An independent
bootstrap analysis over 89 tasks x 5+ trials, task-clustered with
Benjamini-Hochberg correction, found **24 of 25 adjacent leaderboard rank pairs
statistically indistinguishable**, and ~23% of all agent pairs equivalent.

Our sweep runs 2 repetitions per arm, against a measure already known to be
bimodal here: three cap-1 cells on identical inputs scored 39, 40 and 19 of 42.
Two draws cannot separate a treatment from a tree.

The same analysis found something that matters more for what we are doing:
**scaffold choice moved scores more than model choice** (Opus 4.5 spanned 15
points across 4 scaffolds; Haiku 4.5 spanned 22 across 2). That is the thesis of
this whole exercise, independently measured, and it is the reason to spend the
repetitions rather than the reason to skip them.

**Partly acted on**: the matrix no longer spends two cells re-recording
"default reasoning, no contract". That arm already has FOUR samples (three
smoke cells plus `control-a`), so a fifth bought a control we are holding. Its
cells went to the sandwich instead.

**Still open**: raise repetitions on the arms that survive the first pass
rather than widening the matrix further. A third arm at n=2 buys less than a
second arm at n=5.

## Two corrections to the article that sent us here

The secondary write-up compressed Vercel's architecture in a way that matters
for us. Their agent had **17** tools, not sixteen, and it was replaced by
**two**, not one: `ExecuteCommand` (bash in a sandbox) AND a retained
`ExecuteSQL`. The side-effecting action stayed behind its own narrow interface
even in the radical simplification. If we ever generalise a tool here, that is
the shape to copy.

Their own stated boundary condition, verbatim: "This only worked because our
semantic layer was already good documentation... If your data layer is a mess
of legacy naming conventions and undocumented joins, giving Claude raw file
access won't save you. You'll just get faster bad queries." Our equivalent of
that semantic layer is the contract, which is exactly why the contract stage
and the shell-heavy merge are the same question.

## What the evidence says NOT to do here

**Do not cut tools.** Vercel's result is about deleting a hand-built
summarisation layer in front of a capable model, not about tool count as such,
and their own credit goes to "getting out of the model's way". We already offer
five real tools. The one controlled study on tool count (arXiv 2605.24660)
measures showing ~7 of 370 adaptively; it has nothing to say about a surface of
five.

**Do not build loop detection.** LangChain's fires on repeated edits to the
same file and is an advisory nudge the model may ignore. Our repeat rate keyed
on tool name AND arguments is 24 of 466 calls. There is nothing here for it to
catch.
