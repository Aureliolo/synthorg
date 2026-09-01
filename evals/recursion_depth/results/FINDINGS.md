# Findings from the 2026-09-01 depth-1 smoke

Everything the three-cell smoke surfaced: defects, design flaws, measurements
that overturn a stated assumption, and improvements worth making. One list,
because the register that fed it lived in a gitignored audit and the individual
cell READMEs each see only their own cell.

Ordered by consequence. **Validity** decides whether a matrix would measure
anything; **cost** decides what it would cost; **product** are defects in
shipped code rather than in the harness; **evidence** are things that made
measuring harder; **corrected** records claims this run made and then withdrew.

The run: three depth-1 gated cells, one variable (executor sampling), same spec,
same reviewer configuration, 80,707,895 tokens over 2,010 calls in 5.49 hours.
Scores 39, 40 and 19 of 42.

---

## Validity: these decide whether the matrix measures anything

### V1. No contract stage, while the product mandates one

The harness goes plan -> leaves -> merge. The product mandates `SKELETON`
between APPROVED and EXECUTING, with no `APPROVED -> EXECUTING` edge at all.
So the sweep measures a loop the product does not run.

Measured consequence: essentially every module written by more than one child
has a different interface in each (**11 of 11** in cell A, **10 of 11** in B,
**11 of 12** in C). `errors.py` was written by all eight children with eight
vocabularies. `lexer.py` exports `lex`, `tokenize` and `tokenise` in three of
them.

Live confirmation, not just static: every merge failure in all three cells was
one class, a name the caller expected and the writer spelled differently
(`cannot import name`, `No module named`). Not one was a logic bug or a failed
assertion.

**This is the root cause most of the rest hangs off**, and the damage scales
with depth: a cap-4 tree has far more shared surface to disagree about.

### V2. The headline metric cannot distinguish verified work from unverified

`oracle.py` grades behaviour with its own held-out tests and never reads the
unit's own suite. So:

| Cell | test files in the scored tree | score |
| --- | ---: | ---: |
| A | **0** | 39 |
| B | 13 | **40** |

One point apart. A reader of `merged_passing` cannot tell which tree was
verified. At depth this compounds invisibly: a sub-merge that drops its
children's tests hands its parent an unverified subtree the final number cannot
distinguish from a sound one.

**Fixed in this branch** (`feat: report how many tests a scored tree carries
beside the score`): `count_test_files` on the produced-tree fingerprint, carried
down the existing `UnitDelivery -> MergeOutcome -> UnitRecord -> report` chain,
and rendered beside `Satisfied` in the per-cell table rather than only in the
per-merge table.

### V3. Repair rounds float with reviewer behaviour, so cells are not comparable

| Cell | merge attempts | reviewer suite runs | verdict |
| --- | ---: | ---: | --- |
| A | **1** | 1 | approve_with_notes |
| B | 3 (cap) | 11 | reject |
| C | 3 (cap) | 19 | reject |

The sweep holds the sampling axis constant and lets the REPAIR budget vary,
which is backwards. One cell got a third of the repair budget the other two got
and it is the one that scored highest.

### V4. Reviewer rigour varies 19-fold on a byte-identical configuration

The reviewer's four dials and its connection sha256 are **identical across all
three cells**. So the 1/11/19 spread in suite runs is draw variance on a fixed
config, not a second treatment axis.

That is the worse of the two readings: a dial the sweep sets could be pinned; a
session-to-session draw cannot. The gate's strictness is free to vary between
any two cells the matrix compares, including two repetitions of the same cell.

### V5. The result is bimodal, and a single collapse would read as a depth effect

Scores 39, 40, **19**. Two draws agree within one point; the third collapses 21
points. Not smooth variance: "usually about 40, occasionally catastrophic".

For a design budgeting 2-3 repetitions per cap, one collapse moves a cap mean by
7 to 10 points, which is larger than any depth effect the pilot ever claimed.
And the remedy differs from the one smooth variance would need: more repetitions
dilute a collapse without detecting it.

### V6. The scores order monotonically with temperature, and that is a trap

0.6 -> 19, 0.7 -> 39, 1.0 -> 40 reads as a clean dose-response. It is not:

1. A and B differ by ONE point across a 0.3 gap, so the entire apparent effect
   is C's collapse.
2. C's collapse has a traced non-sampling mechanism (V1: two modules nothing
   provides).
3. The cells did different jobs (0 vs 13 test files) under different repair
   budgets (V3).

**The sampling axis is unanswerable by this design**, and that is a design
problem rather than a sample-size problem.

### V7. Leaves are cut off, not finished

**58% terminate `budget_exhausted`** at `unit_token_ceiling: 1500000`, and one
leaf claimed 18 of the 42 requirements on its own. Leaf medians land within 6%
of each other across all three cells because they are all hitting the same
ceiling rather than finishing.

Downstream of V1: with no agreed slice, each leaf builds a full vertical stack.
Sizing and ceiling are the same problem twice.

---

## Cost

### C1. Prompt caching was OFF for all 80,707,895 tokens, and nobody chose it

```
"reason": "model_lacks_caching_support"
"event": "provider.prompt_caching.skipped"
```

Emitted on **every call**: 550, 712, 728 across the three cells.

Chain: `litellm_model_info.py:309` reads `info["supports_prompt_caching"]` from
LiteLLM's static registry. LiteLLM does not know `glm-5.3-flash` (a custom
OpenAI-compatible endpoint), so the key is absent, the code falls back to
`ModelMetadata`, and that field is `Field(default=False)`. The provider config
declares `supports_tools`, `supports_reasoning`, `family` and
`max_output_tokens`, and not this one.

**CORRECTION, measured after this register was first written: turning caching on
would have saved nothing.** The claim that this was "plausibly the largest single
cost lever" is withdrawn.

Probed directly, with `cache_control` placed by hand and our own mapper bypassed
entirely, two calls sharing a 63,899-character prefix:

```
prompt_tokens_details: null      <- no cached-token breakdown at all
prompt_tokens: 15270             <- identical on both calls
```

Nothing cache-shaped anywhere in the response. **Ollama's OpenAI-compatible
endpoint neither reports nor bills prompt caching**; the markers are accepted and
ignored. So `model_lacks_caching_support` was a TRUE statement about this
endpoint, and the run lost no money to it.

**What survives is the mechanism, not the loss.** The capability was decided by a
silent `Field(default=False)` rather than by knowledge, and it happened to be
right here by luck. That is still P2 and still worth fixing: a probe would have
answered this in seconds instead of by assertion. But the case for fixing it is
correctness, not savings.

**The cost problem itself is untouched.** Input still outweighs output 10 to 1
(74M against 6.7M). Caching was never the lever; C2 names the real one, and a
contract stage that shortens the read phase is the only thing that reaches it.

**Same defect class as the `reasoning_effort` drop already on record, and as
`max_context` having to be hand-probed. Three instances. See I1.** Note the
contrast that makes the probe methodology sound: this same endpoint DOES surface
`reasoning_content` (measured on every reviewer response in the smoke), so an
absent cache field is a real answer about caching rather than an endpoint that
reports nothing at all.

### C2. Cost is context re-send, so neither `max_tokens` nor budget can help

Cell-wide input:output is 10:1; a merge alone is **30:1** (cell A's merge: 96.8%
of its spend was input). Every file read is re-sent on every later turn, so cost
is quadratic in the length of the read phase.

The ratio WIDENED as the run progressed (9:1 at 53.5M, 10:1 at 80.7M), which is
the mechanism confirming itself.

The levers that bite are shortening the read phase (a contract stage) and not
re-reading on a retry. Raising `max_tokens` cannot help; output is a tenth of
the bill.

### C3. The merge forages rather than builds: 83% of its tool calls are shell

Read from actual `tool_calls` (parsing SSE deltas), not from request schemas:

| role | calls | `shell_command` | write + edit |
| --- | ---: | ---: | ---: |
| leaf | 1,225 | 34% | **56%** |
| **merge** | 577 | **83%** | **13%** |
| review | 552 | 91% | 0% |

The leaves are healthy: a third shell, a third write, a quarter edit is a normal
build loop. The merge makes 480 shell calls against 76 file changes. Combined
with C1 and C2, every one of those 480 calls re-sends the whole accumulated
conversation uncached.

### C4. Merge cost is a function of attempt count, and the retries buy little

| Cell | merge attempts | merge tokens | merge share of cell |
| --- | ---: | ---: | ---: |
| A | 1 | 9,014,167 | 46% |
| B | 3 | 20,916,747 | **70%** |
| C | 3 | 21,309,323 | **70%** |

The two three-attempt merges land **within 2% of each other** on different
plans and different trees, so:

```
merge ≈ 9M for the first attempt + ~6M per retry
```

Attempts came out 1, 3, 3, so two of three cells hit the cap. At depth every
internal node is a merge carrying its own independent draw from that
distribution.

### C5. A merge retry fixes forgotten files and never semantic divergence

- **Fixed by a retry**: B's `tests/conftest.py`, failing at 03:07 and carried up
  by 04:05. A file the merge forgot to copy is one visible gap.
- **Not fixed**: `sqlcsv.csvio` and `sqlcsv.aggregation` unimportable in C
  across FOUR windows spanning 2.5 hours and all three attempts.
  `SemanticError` and `EXIT_NOT_WIRED` likewise in B from 03:07 to 04:05.

So `merge_attempts: 3` is not the lever. The budget buys the cheap class of fix
repeatedly and never reaches the expensive one. Raising the cap would not help.

### C6. A cap-1 cell costs roughly twice the declared figure

Declared ~14M. Measured 19.6M / 30.2M / 30.9M, mean 26.9M. The three cells
consumed **192% of the ~42M the plan projected for them**.

Extrapolated, the matrix is **900M to over 1B** rather than the plan's 717M, and
the upper end is not bounded by anything measured. Treat 900M as a floor: it was
derived from the cap-1 overrun, and cap 1 is the class where the retry premium
is smallest because it has one merge.

---

## Product defects (shipped code, not the harness)

### P1. Every sandbox container reads `unhealthy` for its whole life

Measured on a live merge sandbox: `State.Health.Status = unhealthy`,
`FailingStreak = 107`, one probe failure every ten seconds since start.

Two lines that do not know about each other:

- `docker/sandbox/Dockerfile:46` sets
  `CMD ["sh","-c","python3 /usr/local/bin/healthz.py & sleep infinity"]`, so the
  image's default command starts the server its own `HEALTHCHECK` (line 43)
  dials on `127.0.0.1:15003`.
- `tools/sandbox/docker_sandbox_exec.py:81-82` declares
  `_KEEPALIVE_COMMAND = "tail"` / `_KEEPALIVE_ARGS = ("-f","/dev/null")`, passed
  to `_build_container_config` at `:574`, **replacing that CMD**.

Confirmed on the container: `Config.Cmd` is `["tail","-f","/dev/null"]`.

**Its own test file is the proof of the class.**
`tests/unit/tools/sandbox/test_sandbox_healthcheck.py` opens by describing a
PREVIOUS fix for this exact symptom (the probe called `wget`, absent from Wolfi,
"FailingStreak 30 in a measured run"). Five tests were written and all five
pass. Every one exercises the probe in isolation, and
`test_it_succeeds_against_a_served_endpoint` starts its OWN stub server, which
is precisely the step production never performs.

Impact is bounded: nothing gates on a sandbox's Docker health (the sidecar path
that does poll `State.Health` and raise uses a different image whose server does
run). The cost is observability, permanently stuck at the failing value.

Fix is a choice between the two owners, not a patch to both: either the
keep-alive command starts `healthz.py`, or the image drops a `HEALTHCHECK` its
only consumer prevents from passing.

### P2. Capability resolution fails silent for any model LiteLLM does not know

The general form of C1. `ModelCapabilities` is resolved from LiteLLM's static
registry, falls back to `ModelMetadata` defaults, and every unknown capability
defaults to `False`/absent with no signal. Three instances observed:

1. `supports_prompt_caching` -> caching off for 80.7M tokens (C1)
2. `reasoning_effort` silently stripped (already on record)
3. `max_context` had to be measured by hand via `POST /api/show`

**An operator must know to declare twelve fields correctly, and getting one
wrong is invisible.** See I1 for the proposed fix.

---

## Evidence and operations

### E1. The transcript tap corrupts under concurrency

~3-8% of lines unparseable depending on the run, and it clips line tails so
`finish_reason` is lost on exactly the largest responses. Transcript ABSENCE
never proves tool absence.

Per-response token usage is not recoverable from transcripts at all, because a
streamed response records as raw SSE frames; it comes from `logs/cost_usage.log`.

### E2. Concurrent cells poison each other's resume identity

| Cell | `git_commit` | `git_dirty` |
| --- | --- | --- |
| A | `02a917c0c` | **false** |
| B | `02a917c0c` | **true** |
| C | `02a917c0c` | **true** |

`_dirty_argv` excludes the recorder's OWN out-dir by pathspec, and its docstring
gives the right reason: the recorder's own output is not what the flag means. A
concurrent sibling's output is equally not that, and is not excluded. So under
the concurrency the plan explicitly endorses, every cell after the first records
`git_dirty: true` for a reason unrelated to the code under test, and **cell A
can no longer be resumed**.

Fix: generalise the pathspec to exclude every out-dir the operator names.

### E3. Nothing reads the provider's own quota meter

The harness tracks its own token accounting and never asks the provider. During
this run I estimated "62% of a session window" from a 130M figure I invented.

The real reading, taken from the account page afterwards: **session 9.7%,
weekly 4.2%**, with 2,018 requests this week against 2,010 calls in our cost log
(so this week's usage IS the smoke).

**That reframes the cost picture**: ~1.9B tokens per weekly quota, so a 900M-1B
matrix is roughly half a week rather than the binding constraint. Wall clock and
the 5-hour session window are what actually pace a run.

### E4. `delivered` reads False on the best-scoring tree in the harness's history

The flag means "assembled AND its own tests pass". Cell A scored 39 of 42 with
`delivered=False`, because it brought up no tests at all so the suite collected
nothing. Any summary trusting the flag records that cell as a failed merge.

The two travel separately by design and `_delivery`'s docstring says so; the gap
was that the count (V2) was missing, which is now fixed.

### E5. A leaf can spend a whole unit producing a document instead of code

Cell C's first leaf, "Decide engine architecture and shared contracts",
delivered nothing and produced one file. The plan asked for a contract and got
prose. This is what V1 looks like when a planner tries to solve it from inside a
harness that has no stage for it.

---

## Improvements worth making

### I1. A capability DISCOVERY round that verifies effect, not acceptance

The fix for C1 and P2 is not another config field. The critical constraint:
**a 200 response proves nothing**, because the failure mode is a parameter being
accepted and silently dropped. A probe must check the observable consequence:

| capability | how to verify |
| --- | --- |
| prompt caching | send a long prefix twice, read `cached_tokens` on the second |
| reasoning | send `reasoning_effort`, check `reasoning_content` came back |
| tools | send a trivial schema, check a `tool_call` returns |
| context | the `POST /api/show` probe, already done by hand |

Once per connection, persisted, a handful of small calls: free against 80.7M.
There is already a `metadata_source` provenance field (`"preset"` today), so
`"probed"` slots in. **The fail-safe direction should invert**: an unprobed
capability must be loud, not silently `False`.

Two things to check before building it: whether the endpoint surfaces
`cached_tokens` at all (otherwise the probe cannot verify caching and we would
be guessing again), and whether any probe infrastructure already exists.

### I2. A contract stage in the harness, mirroring the product's `SKELETON`

The single highest-leverage change. It removes V1 at source, which removes the
merge's foraging (C3), most of the read phase the cost is quadratic in (C2), and
the failure mode behind C's collapse (V5).

### I3. Hold repair rounds equal across cells

Fixes V3 and neutralises V4. Whatever the number, it must not be set by how
thorough a reviewer session happened to be.

### I4. Carry an inventory into a merge retry

A retry currently re-reads everything (90% of attempt 2's reads repeat attempt
1's) and then fails on what it failed on before (C5). Handing it the previous
attempt's map would attack both halves.

### I5. Report tests-carried beside the score

**Done** (V2).

### I6. Read the provider quota into the harness

E3. A run that cannot see the meter it is bounded by cannot pace itself, and the
operator gets an invented number instead of a reading.

---

## Corrected during the run

Recorded because each was stated confidently first, and a register that hides
its own withdrawals is not one.

| Claim | Correction |
| --- | --- |
| "The metric REWARDS skipping tests" | **Withdrawn.** Built on A (39, no tests) and C (19, ten tests). Cell B carried all 13, spent the full repair budget, and scored highest at 40. The metric is *blind* to verification, not biased toward omitting it. Skipping was cheaper (19.6M for 39), not better |
| "The noise floor is a 20-point spread" | Refined: it is **bimodal** (39, 40, 19), which needs a different remedy |
| "R06, R22, R35 are intrinsically hard, they defeated both cells" | **Wrong**, drawn from A and C only. Cell B passes all three. Every one of the 42 is passed by at least one cell |
| "The smoke is ~15% of a session window" / "62%" | **Withdrawn.** Built on an invented 130M figure. Real: 4.2% of a WEEKLY quota (E3) |
| "Caching off is plausibly the largest single cost lever" | **Withdrawn (C1).** Probed the endpoint directly: `prompt_tokens_details: null`, identical `prompt_tokens` on a repeated 63,899-char prefix, nothing cache-shaped in the response. Ollama's endpoint does not report or bill prompt caching, so enabling it would have saved nothing. The silent-default MECHANISM is still a defect; the loss was not real |
| "Cell B used 2 merge attempts" | It used **3**, the cap. Read mid-run and not re-checked |
| "attempts=2 means two merge attempts" | It counts SESSIONS (merge + review). Cell A ran ONE merge |
| Looping medians 0.331/0.164/0.209 | Measured the SSE protocol envelope as text. Real medians all 0.000 |
| Thinking share 96-99% | Excluded `tool_calls.arguments`, where an agentic session's work product lives. Real: 72-79% |
| "100% leaf delivery (7/7)" | Conflated `produced` with `delivered`. Real: A 5, B 5, C 3 of 8 |
| Registered prediction: A and B would lose R29-R32 | **Falsified.** A implements the most aggregation of the three |
| "Zero review suite runs in cell A" | Measured figure is **one**. The grep only searched lines a write-classifier had printed |
| "A merge-failure path exists" | It does not. `merge.py:460-497` ends the loop and grades whatever tree exists |

## Fixed in this branch

- **V2 / I5**: tests-carried reported beside the score.
- A naming defect in that fix's own code: the helper was first called
  `test_files_in`, which **pytest collected as a test function** when the test
  module imported it. Renamed `count_test_files`.
