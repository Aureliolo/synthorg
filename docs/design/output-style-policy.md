---
title: Output-Style Policy
description: Configurable house writing style injected into agent prompts, plus a deterministic hard guardrail that rejects or rewrites agent output violating a hard rule at every output boundary.
---

# Output-Style Policy

> Give the synthetic organisation a way to control and enforce the writing style
> of what its agents produce, so the output does not read as obviously
> AI-written and can be held to hard house rules such as no em-dashes.

**Module**: `src/synthorg/engine/output_style/`

The subsystem has two layers driven by one pluggable pack:

- **Soft**: a configurable house writing style injected into the agent system
  prompt (the style the organisation asks agents to write in).
- **Hard**: a deterministic guardrail that inspects agent-generated output at
  every output boundary and rejects or rewrites it when it violates a hard rule.
  A prompt directive alone is not enforcement, because the model may ignore it,
  so the hard layer is the load-bearing part. The hard path never calls an LLM.

## Key principle: segmentation picks the action, sanction grants the pass

The guard polices all agent-generated output (prose and code) by default.
Structure-awareness exists only to pick a safe enforcement action, never to
grant a pass:

- **Corruption safety**: rewriting an em-dash to a comma inside a code span or a
  string literal could corrupt the program. So a deterministic `segmenter`
  (`segmenter.py`, over Markdown fences, inline-code spans, and code channels)
  decides which action is safe: a prose span with a safe transform may
  auto-rewrite; a match inside a code span is rejected, never rewritten.
- **False-positive suppression**: a backslash-u escape or an HTML `mdash` entity
  reference is ASCII, not a violation. A literal U+2014 is a violation even in a
  code comment.

Syntactic position never grants a pass. Only a matching operator-sanctioned
scope does (see [Sanctioned exemptions](#sanctioned-exemptions)).

## Soft layer: house writing style

`HouseStyleDirective` entries render into a `## House Writing Style` section of
the agent system prompt, after Personality and before Skills, via
`adapter.inject_house_style_context`. Directives are scoped org-wide or per
role or per department, reusing the `ScopeKind` (`ALL` / `ROLE` / `DEPARTMENT`)
mechanism the strategy module uses for constitutional principles: an agent receives
every `ALL` directive plus the `ROLE` and `DEPARTMENT` directives that match it.

The provider is a process-global ambient snapshot (`provider.py`), set at boot
and refreshed by the settings subscriber. The prompt build resolves it once and
threads that single snapshot through both the injection and the section-manifest
read, so the two agree even if an operator hot-swaps the pack mid-build. Even if
a future token-trimming pass were to drop this section under budget pressure,
enforcement is unaffected, because the hard gate is independent of the prompt.

The prompt states which directives are hard-enforced (the em-dash ban is
rejected at the boundary) versus expected-and-monitored (the fuzzy signals), so an
agent does not learn that the section carries no consequence.

## Hard layer: deterministic guardrail

### Rules and enforcement modes

An `OutputStyleRule` is a literal ban or a regex ban with one or more patterns,
a message, and an enforcement mode:

| Mode | Behaviour |
|------|-----------|
| `reject_rework` | Fails closed and routes the output back to the producing agent with the specific rule, the default. |
| `shadow` | Computes and surfaces the finding but never blocks, for the fuzzy heuristics, so a noisy rule cannot wedge the organisation. |
| `auto_rewrite` | Applies a deterministic safe transform in a prose span only. A match in a code span downgrades to `reject_rework`, because a punctuation swap could corrupt code. Off in the default pack. |

A global `shadow_mode` forces every rule to shadow for an observation period.
The `OutputPolicyEvaluator` (`evaluator.py`) compiles the patterns once and
returns an `OutputPolicyVerdict` with per-match findings, an optional rewritten
text, and a summary.

**A block names its places, not just its rule.** The rework loop hands the
summary back as the agent's next turn with "address that specifically", so the
summary carries the clause around each match as well as the rule's message.
A literal ban matches a single character: told only that the character is
banned, an author has to re-read a whole deliverable to find it and is as
likely to rewrite around it as to remove it. A live run failed a deliverable
its peer reviewer had already approved, after three rework rounds that never
located the four em-dashes in it. Each finding therefore carries a `context`
window, which only the evaluator can produce because only it knows where the
match landed, and the verdict quotes up to `MAX_QUOTED_PLACES` of them before
it starts counting the rest.

### Output boundaries

The `interceptor.py` helpers (`enforce_output_policy` raises on a block;
`evaluate_output_policy` returns the verdict) are called at every agent-output
boundary before the output escapes:

| Boundary | Site | Channel |
|----------|------|---------|
| Inter-agent message | `communication/messenger.py` + `communication/messages/service.py`, via the shared `communication/_output_guard.py` `guard_message_output` (which applies any auto-rewrite back onto the message) | `message` |
| Commit message | `tools/git_tools.py` `GitCommitTool.execute` | `commit_message` |
| Code file write | `tools/file_system/write_file.py` (whole content) + `edit_file.py` (the replacement text) | `code_file` |
| Issue / PR body | `tools/forge/forge_tools.py` (`ForgeIssueTool` / `ForgePullRequestTool` open / comment / review), and `meta/appliers/code_applier.py` for the self-improvement PR title / body | `pr_body` |
| Completing deliverable | `engine/_review_oracle_gates.py` `apply_output_policy_gate`, run before the adversarial red-team / vision gates | `deliverable` |

The message boundaries share one helper so an auto-rewrite is applied
consistently at both. The code-file and forge boundaries are code-channel
(reject, never auto-rewrite): a banned literal in agent-written code or an
issue/PR body is rejected before it lands, unless a matching operator-sanctioned
scope (a `path` exemption for code files) covers it.

The per-tool interceptors give the producing agent instant feedback, like a
pre-tool-use hook denial: the tool call fails with the specific rule and the
agent reworks. The completion-gate check is a defence-in-depth backstop, so
even a deliverable that reached completion by a path that skipped a guarded tool
cannot ship a hard-rule violation. In-session output that never escapes the
session is not gated.

### Sanctioned exemptions

The narrow case where output legitimately contains an otherwise-banned literal
(the deliverable is a text-filter product, a linguistics document, a regex, or a
test fixture) is handled by operator-authored sanctioned scopes, never by agent
self-grant. A `SanctionedExemption` keys on a dimension:

```yaml
exemptions:
  - rule_id: emdash_literal
    scope_kind: path        # or task_type, project, department, role, deliverable_tag
    match: "src/textfilter/**"
    reason: "Deliverables implement an em-dash filter"
```

An agent is granted an exemption only when its output context matches a
sanctioned scope for the offending rule. An inline `output-style-allow` marker
is parsed at the boundary and logged as an `OUTPUT_STYLE_EXEMPTION_REQUESTED`
audit event, but never grants a pass on its own, so a lazy or adversarial model
cannot bypass a hard ban by emitting the marker.

Every operator-authored regex rule is validated at pack load: it must compile
and must not contain a nested unbounded-quantifier construct, so a
catastrophic-backtracking pattern cannot reach the hot path and DoS every
boundary. An invalid rule fails loudly where the pack is loaded.

## Pluggable pack

The pack (`RulePack`) holds the soft directives, the hard rules, and default
exemptions. The loader (`pack_loader.py`) reads a built-in pack or a user pack
from `~/.synthorg/output-style-packs/`, mirroring the strategy principle-pack
loader. The default pack ships the em-dash hard ban in `reject_rework` mode and
the fuzzy AI-writing signals (sycophancy, puffery, filler transitions, the word
"delve", the "it's not just X, it's Y" contrastive construction, over-hedging)
in `shadow` mode.

Banned literals never appear verbatim in committed source. The em-dash character
is expressed by its integer code point and its HTML entities by a convenience
flag, both expanded to literals in the loader, so the repo `check_no_em_dashes.py`
gate never receives a literal. Tests build the real character at runtime with
`chr(0x2014)`.

## Configuration and wiring

Settings live in the `output_style` namespace (`settings/definitions/output_style.py`):
`enabled`, `shadow_mode`, `pack`, `house_style_enabled`, and `exemptions` (a JSON
array). All are Category-1 and hot-reloadable: `OutputStyleSettingsSubscriber`
rebuilds the `OutputStylePolicyService` and re-binds the ambient service and
house-style provider on any change, so a pack swap or a toggle takes effect on
the next boundary check and prompt build with no restart. Pack loading is
blocking file I/O, so the rebuild runs it off the event loop. If a bad pack name
or an invalid pack cannot load, it falls back to the built-in default; if even
the default cannot load (a corrupted resource), it falls back CLOSED to an
in-code em-dash ban rather than leaving the guardrail unbound, so enforcement
never silently disables.

Disabling `enabled`, enabling `shadow_mode`, adding an exemption, or swapping the
active `pack` weakens the guardrail (a different pack can drop every hard rule),
so those writes route through the security-write governance guardrail (confirm,
reason, actor) in `settings/write_governance.py`. The `exemptions` payload is
also shape-validated at write time (`settings/json_validators.py`) so a
malformed entry is rejected then, not silently dropped at the next rebuild.

The service is bound at boot by `api/lifecycle_helpers/output_style_wiring.py`
`wire_output_style_policy` (invoked from `api/lifecycle_assembly.py` after the
feature-wiring pass), with reachability locked in the anti-ghost manifest.
The reachability of the boundary guards is locked by
`scripts/check_output_boundaries_guarded.py`.

## Scope

Applies to agent-generated output only. Raw human input persisted in transcripts
stays verbatim (SEC-1: human content is fenced at the LLM boundary, never
rewritten in storage). Vendor-agnostic, British English default, no locale
privileged.

## See Also

- [Verification & Quality](verification-quality.md): the completion-gate chain the deliverable backstop composes into.
- [Strategy](strategy.md): the principle-pack pattern and `ScopeKind` scoping the soft layer reuses.
- [The Org Asks](org-questions.md): the sibling soft-prompt subsystem, registered the same way and scoped by the same `ScopeKind`.
- [Agents](agents.md): the persona and prompt pipeline the soft layer injects into.
