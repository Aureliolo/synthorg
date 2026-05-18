---
title: "GitButler Concept-Only Concurrency Evaluation"
issue: 1978
sources:
  - "https://docs.gitbutler.com/features/virtual-branches/virtual-branches"
  - "https://github.com/gitbutlerapp/gitbutler/blob/master/LICENSE.md"
  - "https://blog.gitbutler.com/opening-up-gitbutler"
  - "https://trigger.dev/blog/parallel-agents-gitbutler"
date: 2026-05-18
---

# GitButler Concept-Only Concurrency Evaluation

**Issue**: #1978 (concept-only research, informs #1974)
**Status**: Decided 2026-05-18

## Bottom line

The agent-concurrency default for [#1974](https://github.com/Aureliolo/synthorg/issues/1974) stays as the issue framed it: **git worktree-per-agent OR branch-per-agent plus a coordinator-owned merge queue**. This study did not produce evidence that a clean-room virtual-branch layer is clearly superior, because the GitButler model leaves the two problems #1974 must actually solve unsolved: forge-level branch collision and concurrent same-file edits.

There is real value here, and it is bounded:

- **The ownership-map concept is adopted as clean-room design vocabulary for #1974**, scoped to one optional future mode (a single shared workspace with N logical lanes) for the case where N physical worktrees are too heavy. It is a supplementary concept, not a replacement for the permissive default.
- **No GitButler code is copied, vendored, or depended on.** GitButler is FSL-1.1-MIT; SynthOrg distributes under BUSL-1.1. Code ingestion is forbidden. Concepts and ideas are not copyrightable and are safe to learn from.
- **As a developer tool on the SynthOrg repository itself**, GitButler is licence-clean (FSL permits internal use) and zero-cost, but a weak practical fit. It is an optional individual choice, not a team mandate.

Revisit GitButler-as-a-tool around **2027 H2**, keyed to agent-API maturity, a GitButler v1.0, and the FSL clock (see "Revisit horizon").

## Context

[#1974](https://github.com/Aureliolo/synthorg/issues/1974) (persistent project workspace + pluggable git + agent-concurrency model), under [EPIC #1973](https://github.com/Aureliolo/synthorg/issues/1973), must let N agents edit one project repository concurrently without branch collision or checkout thrash. Today the sandbox mounts an ephemeral `/workspace` per tool call (see [Deployment](../design/deployment.md#sandbox-image-resolution)); a persistent git-backed project workspace that survives across agents, tasks, and sessions is not yet a defined thing. The agent runtime itself is in active development (see [Design Overview](../design/index.md) and the [Roadmap](../roadmap/index.md)).

GitButler's virtual-branch ownership-map model was flagged as a candidate concept worth understanding before #1974 commits to a mechanism. This page is the clean-room study of that concept and the explicit recommendation feeding the #1974 decision. It introduces no code dependency.

## How the virtual-branch ownership-map model works

The following is a clean-room conceptual description from the public documentation and write-ups. No GitButler source was read or copied.

- **Single shared working directory.** Unlike git's one-branch-at-a-time checkout, multiple virtual branches ("lanes") are applied to the same physical working tree at once. There is no per-branch directory.
- **Ownership map.** Each uncommitted change (at file and hunk granularity) is assigned to exactly one lane. The ownership map is the data structure that records "this hunk belongs to lane A, that file belongs to lane B". Each lane has its own staging area, so commits can be made per lane independently.
- **Commit extraction.** Committing a lane produces a commit containing only that lane's owned changes. Because every lane is extracted from a single working directory that already represents the merge product of all lanes, lanes that are created this way merge cleanly with each other by construction.
- **CLI mutation model.** The `but` command exposes the model to agents and scripts: status with structured JSON output, targeted commits that name specific changes and a target lane (commit IDs are short 2-to-3-character handles), an "absorb" operation that auto-amends loose changes into the commit they logically belong to, and per-lane push and pull-request creation (`but push <lane>`, `but pr new <lane>`).
- **Agent integration.** Two integration shapes exist: an automatic hook model that assumes one agent session equals one branch, and a skill-file model where the agent drives the `but` CLI directly and can distribute one session's changes across several lanes (feature code on one lane, docs on another, SDK types on a third).

The conceptually valuable idea for #1974 is the **ownership map plus single workspace**: N logical branches without N physical checkouts, which directly avoids the disk and checkout-thrash cost of worktree-per-agent.

## Known constraints (the four from #1978)

1. **Virtual branches are a local abstraction; the forge-level collision is unsolved.** Lanes exist only in the local working tree. To integrate work, each lane is still pushed as its own real remote branch (`but push <lane>`, then `but pr new <lane>`). N agents still produce N real remote branches and N pull requests. The coordination problem #1974 must solve, who merges what and in what order at the forge, is the part the model does not address.
2. **Agent support is narrow and early.** Integration is via a CLI plus a skill file or hooks. The automatic hook model hard-codes "one session equals one branch", which is the wrong shape for an orchestrator that assigns work by task rather than by session. The richer skill-file usage is documented as a community pattern, not a stable supported API.
3. **Pre-1.0.** GitButler has not reached a 1.0 release. Its own users document that overlapping edits to the same file across lanes "get messy" because all lanes share one physical working directory. Concurrent same-file edits by different agents are precisely the hard case #1974 must be safe under, and it is the model's documented weak spot.
4. **FSL-1.1-MIT is incompatible with BUSL-1.1 distribution.** GitButler is licensed FSL-1.1-MIT: a non-compete source-available licence whose every release converts to MIT on the second anniversary of that release. SynthOrg distributes under BUSL-1.1. Bundling, vendoring, or copying FSL code into a BUSL-distributed product is not permitted. Studying the concept and building a clean-room layer from the public description is permitted, because ideas are not copyrightable; only the expression (the code) is licensed.

## Evaluation matrix

The candidates are the boring permissive options the issue names plus the GitButler-style concept as a clean-room layer.

| Dimension | Worktree-per-agent | Branch-per-agent + coordinator merge queue | Clean-room virtual-branch concept (single workspace + ownership map) |
|---|---|---|---|
| Solves forge-level collision | Partial (still N branches; coordinator still needed to merge) | **Yes** (the merge queue is the single serialisation point) | No (still N real branches pushed; identical to branch-per-agent at the forge) |
| Concurrent same-file edits | Safe (physically isolated trees) | Safe (physically isolated branches/checkouts) | **Weak** (lanes share one physical working tree; documented as the model's failure mode) |
| Isolation strength | **Strongest** (separate working tree per agent) | Strong (separate ref per agent) | Weakest of the three (shared tree, logical separation only) |
| Operational weight | Higher (disk per tree, checkout thrash, per-tree services if a dev stack is involved) | Low (refs are cheap; ephemeral sandbox container per agent already exists) | Lowest disk cost (one tree) but new bespoke subsystem to build and maintain |
| Licence-clean to build in SynthOrg | **Yes** (native git) | **Yes** (native git) | Yes only if clean-room from the concept; never from FSL code |
| Maturity of the underlying mechanism | **Mature** (git worktree is core git) | **Mature** (git refs + standard merge-queue patterns) | Immature (pre-1.0 upstream; bespoke if reimplemented) |
| Fit to SynthOrg's ephemeral-sandbox model | Good (a worktree maps to a mounted volume per agent) | **Best** (a branch per agent task; sandbox already ephemeral per call) | Uncertain (a persistent shared tree must be safe under concurrent container mounts) |

Branch-per-agent plus a coordinator-owned merge queue is the only column that solves forge-level collision as a property of the design rather than deferring it. That is the issue's stated default, and the matrix confirms it.

## The four direct questions

These are distinct axes and are easy to conflate. Each is answered separately.

### 1. Should SynthOrg the product take a code dependency on GitButler?

**No.** FSL-1.1-MIT cannot be bundled, vendored, or copied into a BUSL-1.1-distributed product. This is a hard licensing boundary, not a preference. It is the same class of constraint recorded for other dependencies in [`docs/licensing.md`](../licensing.md).

### 2. Should SynthOrg adopt the concept clean-room?

**Partially, and scoped.** Concepts are not copyrightable, so a clean-room ownership-map layer built only from the public description is legally permitted. It is worth adopting as **design vocabulary for #1974**: keep "single shared workspace with N logical lanes via an ownership map" as a named, optional future mode for deployments where worktree-per-agent is too heavy. It is explicitly not adopted as the primary mechanism, because it does not solve forge-level collision and is weak under concurrent same-file edits, which are the two properties #1974 most needs. The clever `absorb` and per-change commit-targeting UX is noted as prior art and not adopted; SynthOrg's coordinator-merge-queue model does not have the manual-rebase problem `absorb` solves.

### 3. Should we use GitButler as a developer tool on the SynthOrg repository itself?

**Optional individual choice, not a team mandate.** It is licence-clean (FSL explicitly permits internal use) and the client is free, so there is no legal or cost barrier. The reason it is not recommended for the team is fit, not worthlessness:

- The headline pitch ("ditch worktrees") targets the pain of running a heavy multi-service dev stack per worktree (separate Postgres, Redis, and similar). SynthOrg worktrees mostly run tests, not a long-lived multi-service dev server, so that specific pain is largely absent here.
- The repository's parallel-worktree workflow, git hooks, and tool-call gates are already invested around worktrees (see the worktree tooling and the project memory on worktree-hooks isolation).
- It is pre-1.0 with a documented same-file weakness.

A developer who personally prefers the `but` CLI plus a Claude Code skill for juggling parallel agent work can use it on their own clone without affecting anyone else. It changes neither the product nor the distribution licence.

### 4. What does it cost?

The client is **free** with no mandatory payment and works without GitButler's servers. An optional **$5/month supporter tier** removes a cosmetic committer mark and adds perks. The vendor plans future paid server and team features that do not gate standalone client functionality. There is **zero cost to evaluate or use** GitButler for individual development.

## Recommendation for #1974

1. **Adopt the permissive default.** Agent concurrency in #1974 is branch-per-agent (or worktree-per-agent where physical isolation is needed) plus a coordinator-owned merge queue. The merge queue is the single forge-level serialisation point and is the part GitButler's model does not provide.
2. **Keep the ownership-map concept as a named optional mode.** #1974's pluggable design should reserve a clean-room "single workspace + ownership map" strategy as an optional future backend for the heavy-worktree case, behind the same protocol plus factory plus config discriminator pattern the project uses everywhere. Ship the boring default; leave the door open.
3. **Introduce no GitButler code dependency, ever, while SynthOrg is BUSL-1.1.** Any future virtual-branch-like layer is clean-room from the public concept only.
4. **Do not block #1974 on this study.** This page is the input; the binding decision is #1974's to make with this evidence in hand.

## Revisit horizon

Re-evaluate GitButler-as-a-tool (not as a code dependency, which the licence forbids regardless) around **2027 H2**. Three independent signals gate a useful revisit:

- A stable, supported agent API rather than a community skill-file pattern.
- A GitButler v1.0 that resolves the documented same-file weakness.
- The FSL clock. FSL-1.1-MIT converts to MIT on the second anniversary of **each individual release**, on a rolling per-commit basis. It is not a single flag-day at v1.0. Any specific GitButler revision becomes MIT exactly two years after that revision shipped, independent of version number. A revisit should check the conversion state of the specific revision in question, not assume "v1.0 plus a year".

Even after FSL converts a given revision to MIT, the recommendation against making it a load-bearing code dependency stands until the forge-level collision and concurrent same-file properties are solved by the upstream model, because those are the properties #1974 actually needs.

## Consequences

- **For #1974**: inherits a settled default (branch-per-agent + coordinator merge queue) and a named optional clean-room mode (single workspace + ownership map). No mechanism is left undefined; the hard problem (forge-level collision) is solved by the default, not deferred.
- **For SynthOrg upstream**: no code change, no dependency added, no licence exposure. This study is documentation only.
- **For individual developers**: GitButler may be used on personal clones at zero cost and zero licence risk; it is neither endorsed nor prohibited for that use.
- **No code dependency introduced.** The acceptance criterion of #1978 is satisfied: a short written recommendation feeding the #1974 concurrency decision, with nothing copied, vendored, or depended on.

## References

- [GitButler virtual-branches documentation](https://docs.gitbutler.com/features/virtual-branches/virtual-branches): ownership, lanes, staging, clean-merge property
- [GitButler `LICENSE.md`](https://github.com/gitbutlerapp/gitbutler/blob/master/LICENSE.md): FSL-1.1-MIT terms, two-year per-release MIT conversion, internal-use permission, non-compete restriction
- [Opening Up GitButler](https://blog.gitbutler.com/opening-up-gitbutler): licence rationale, free client, optional $5/month supporter tier, future paid server features
- [Trigger.dev: parallel agents with GitButler](https://trigger.dev/blog/parallel-agents-gitbutler): real parallel-agent usage, the `but` CLI workflow, the documented same-file weakness, branch-by-branch push and PR creation
- [#1974](https://github.com/Aureliolo/synthorg/issues/1974): persistent project workspace + pluggable git + agent-concurrency model (the decision this feeds)
- [#1973](https://github.com/Aureliolo/synthorg/issues/1973): EPIC, autonomous product studio substrate
- [`docs/licensing.md`](../licensing.md): operator-facing licensing summary
- [Design: Deployment](../design/deployment.md): sandbox image and ephemeral workspace model
- [Design: Distributed Runtime](../design/distributed-runtime.md): agent execution and task-queue concurrency (separate subsystem; not the workspace/git layer)
