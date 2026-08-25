# Inner-Loop A/B Recording

A point-in-time record of the scored A/B that compared SynthOrg's two inner execution loops,
and the reason only one of them ships now. The harness that produced it has been removed along
with the loop that lost; this page is what the measurement leaves behind.

## What was measured

Both loops ran the same workspace-graded coding briefs through the real `AgentEngine.run()`,
the real sandbox lifecycle, and the real LLM-gateway cost boundary. Each brief carried hidden
checks the agent never saw, run against the delivered tree after the session ended, so
correctness was graded on what the work does rather than on what the agent said about it.

The matrix was 2 loops x 3 capability rungs x 5 briefs x 3 repetitions, for 90 recorded runs.
Capabilities bound an explicit `(provider, model_id)` pair; the committed manifest used the
repository's vendor-agnostic placeholder ids.

Scoring weighted correctness 60, resilience 20, tokens 10, latency 5 and turns 5, with a
correctness gate floor of 60. A cell below the floor is disqualified rather than ranked: a
composite that beats another composite while neither delivered is not a promotion signal.

Recorded against commit `cdfb2e1a70f54c3fa51b92c785005cb7458ba96d` on 2026-08-12, brief suite
`sha256:113894e4cfda6693`, manifest `sha256:2ae4b7df1b62d696`.

## What it found

**ReAct scored higher in 12 of 15 cells**, including every expert cell, and in 11 of the 13
where either loop cleared the gate. Per complexity bucket: simple `react 99.3`,
medium `react 97.0`.

**Complex and epic have no winner.** Both loops fell below the gate there, and every
disqualification came from the basic and capable rungs; at expert both scored 100 correctness
on every brief. That finding is about the model rather than the loop, and lives in
[model-capability policy](../reference/model-capability-policy.md).

**The failure shapes differ more than the failure rates, and that is the decisive result.**
Counted by pass rate, ReAct failed 11 of 45 runs and OpenHands 8 of 45, which favours OpenHands.
Counted by how they failed, 9 of ReAct's 11 ended `NO_OP` or `ERROR`, which the zero-artifact
guard terminates `FAILED` so the plan can replan; only 2 ended `completed`. Six of OpenHands'
eight ended `completed`, artifacts written and a confident summary attached, which nothing
downstream of the loop can distinguish from success. Per run that is 2 of ReAct's 45 reaching
review as an apparent success against 6 of OpenHands' 45. For a supervised system the second is
the expensive failure, and no rubric dimension measured it.

## Results

| Brief | Capability | Loop | Score | Correctness | Tokens | Wall-clock | Turns | Rework | Pass rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| loop-ab-bugfix | expert | `react` | 100.0 | 100 | 33001 | 32.5s | 11 | 0+ | 100% |
| loop-ab-bugfix | expert | `openhands` | 90.3 | 100 | 95014 | 63.6s | 13 | 0+ | 100% |
| loop-ab-bugfix | capable | `react` | 94.7 | 100 (40-100) | 34948 | 32.6s | 13 | 0+ | 67% |
| loop-ab-bugfix | capable | `openhands` | 85.7 | 100 | 227199 | 108.3s | 24 | 0+ | 100% |
| loop-ab-bugfix | basic | `react` | 96.4 | 100 | 99629 | 34.9s | 27 | 8+ | 100% |
| loop-ab-bugfix | basic | `openhands` | 91.4 | 100 | 236136 | 78.9s | 27 | 0+ | 100% |
| loop-ab-feature | expert | `react` | 100.0 | 100 | 26487 | 44.1s | 8 | 0+ | 100% |
| loop-ab-feature | expert | `openhands` | 91.4 | 100 | 73373 | 53.4s | 11 | 0+ | 100% |
| loop-ab-feature | capable | `openhands` | 83.5 | 100 | 163569 | 84.6s | 17 | 0+ | 100% |
| loop-ab-feature | capable | `react` (disqualified) | 41.3 | 20 (20-100) | 13319 | 10.7s | 7 | 0+ | 33% |
| loop-ab-feature | basic | `react` | 93.8 | 100 | 389391 | 73.6s | 49 | 23+ | 100% |
| loop-ab-feature | basic | `openhands` | 92.2 | 100 (40-100) | 348128 | 148.0s | 36 | 0+ | 67% |
| loop-ab-pipeline | expert | `react` | 100.0 | 100 | 38902 | 38.2s | 9 | 0+ | 100% |
| loop-ab-pipeline | expert | `openhands` | 91.1 | 100 | 94677 | 59.1s | 12 | 0+ | 100% |
| loop-ab-pipeline | capable | `openhands` | 81.5 | 100 | 159330 | 90.3s | 18 | 0+ | 100% |
| loop-ab-pipeline | capable | `react` (disqualified) | 41.3 | 20 (20-100) | 5094 | 7.1s | 3 | 0+ | 33% |
| loop-ab-pipeline | basic | `react` (disqualified) | 37.5 | 20 (0-100) | 175370 | 72.5s | 32 | 8+ | 33% |
| loop-ab-pipeline | basic | `openhands` (disqualified) | 32.3 | 20 (20-40) | 255176 | 82.1s | 30 | 0+ | 0% |
| loop-ab-refactor | expert | `react` | 100.0 | 100 | 22587 | 26.5s | 8 | 0+ | 100% |
| loop-ab-refactor | expert | `openhands` | 89.4 | 100 | 66360 | 55.4s | 11 | 0+ | 100% |
| loop-ab-refactor | capable | `react` | 92.7 | 100 (20-100) | 68494 | 38.2s | 20 | 1+ | 67% |
| loop-ab-refactor | capable | `openhands` | 91.4 | 100 | 152709 | 87.4s | 21 | 0+ | 100% |
| loop-ab-refactor | basic | `openhands` (disqualified) | 34.2 | 20 (20-80) | 117796 | 44.1s | 15 | 0+ | 0% |
| loop-ab-refactor | basic | `react` (disqualified) | 30.5 | 20 (20-80) | 96629 | 47.4s | 26 | 3+ | 0% |
| loop-ab-simple | expert | `react` | 100.0 | 100 | 6748 | 14.8s | 4 | 0+ | 100% |
| loop-ab-simple | expert | `openhands` | 89.1 | 100 | 27777 | 28.2s | 5 | 0+ | 100% |
| loop-ab-simple | capable | `react` | 98.0 | 100 | 24950 | 22.7s | 10 | 0+ | 100% |
| loop-ab-simple | capable | `openhands` | 94.6 | 100 | 37822 | 37.2s | 6 | 0+ | 100% |
| loop-ab-simple | basic | `react` | 100.0 | 100 | 11075 | 18.8s | 5 | 0+ | 100% |
| loop-ab-simple | basic | `openhands` | 88.4 | 100 (0-100) | 26607 | 20.3s | 5 | 0+ | 67% |

A `+` on Rework marks a cell where provider retries were not observable for that loop, so the
figure counts repeated tool calls only; scoring dropped the retry component for every loop in
such a cell.

## What the recording did not cover

The task shape was narrower than production in two ways that bound what the result generalises
to. The native leg ran five tools (read, write, edit, delete, shell) against the harness's four
in-container tools. That parity was deliberate, since giving one leg the production tool set
while the other could not have it would measure tool availability instead of loop quality, but
it means the matrix never exercised tool selection across a wide catalogue, which is what
progressive disclosure exists for. Each run was also a standalone task: no `plan_id`, no
dependencies, no coordinating lead. So the evidence covers workspace coding tasks and says
nothing about either loop on org work across the MCP surface.

## What happened next

The recording set `engine.default_loop_type = react` and left
`engine.loop_complexity_overrides` empty, which is the configuration that promotes no loop the
measurement does not back.

The second loop was then removed entirely. Beyond losing the comparison, its builder took only
`**_unused: object`, so it silently discarded six in-flight control mechanisms the native
builder receives by name: the approval gate, the stagnation detector, the compaction callback,
the steering inbox, the step classifier, and the checkpoint callback. An operator selecting it
got an ungoverned run with no warning at the selection surface. With one loop shipping, the
selection machinery, its settings and the credentialed-tool MCP boundary that existed to serve
the embedded harness went with it.

## See Also

- [Agent Execution](../design/agent-execution.md): the shipped loop and how a task reaches it
- [LLM Gateway](../design/llm-gateway.md): the cost boundary this recording measured through
- [Model-capability policy](../reference/model-capability-policy.md): where the complex/epic
  finding lives
