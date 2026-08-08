---
title: The Org Asks
description: A standing prompt directive instructs every agent to ask rather than guess when a choice is material and hard to reverse, the two human-input tools park the run, and the resulting question is answerable in the unified conversation.
---

# The Org Asks

> You tell the organisation "build me this", and it asks you questions: at the
> start, and again during implementation, whenever a choice is material and hard
> to reverse. The question reaches you where you already are, in the
> conversation, and answering it there resumes the run.

**Modules**: `src/synthorg/engine/ask_policy/` (the directive),
`src/synthorg/tools/clarification_tool.py` and
`src/synthorg/tools/decision_tool.py` (the two tools that carry a question),
`src/synthorg/api/controllers/chat_questions.py` (the conversational surface).

The feature has three parts that only work together:

- **The ask.** A standing directive in every agent system prompt, worded for the
  agent's autonomy level, telling it to put a material, hard-to-reverse choice
  to a human instead of picking one.
- **The park.** Two agent-callable tools that stop the run, record the question
  as an `ApprovalItem`, and move the task to `AWAITING_INPUT`.
- **The answer.** A question-shaped door on the conversational surface, so the
  operator answers in the chat they are already in rather than hunting for a row
  in the approvals queue.

An organisation with the tools but no directive never asks, because nothing
instructs the agent that asking is expected. An organisation with the directive but
no surface asks into a queue nobody is watching. All three ship on.

## The ask: a standing directive at every autonomy level

`ASK_DIRECTIVES` renders into an `## Asking Rather Than Guessing` section of the
agent system prompt, placed immediately after `## Autonomy`, via
`adapter.inject_ask_policy_context`. The exception to an autonomy licence
belongs directly after the licence that grants it.

The directive is a 4x3 matrix: one text per `AutonomyLevel`, at each of the
three verbosity tiers (`full` / `summary` / `minimal`) the prompt profile
selects for the model in use, mirroring how `AUTONOMY_INSTRUCTIONS` is already
tiered. Every cell says the same thing in a way that fits its level:

| Level | The standing rule |
| --- | --- |
| `FULL` | Autonomy is not a licence to guess. A material, hard-to-reverse choice goes to a human; everything else is yours, with the assumption stated. |
| `SEMI` | Ask rather than guess on a material, hard-to-reverse choice. Routine, easily reversible calls are yours. |
| `SUPERVISED` | Ask rather than guess; fold the question into the proposed plan where possible, and raise it the moment a fork appears mid-step. |
| `LOCKED` | A material, hard-to-reverse choice is never yours to settle quietly. Surface the fork with its alternatives while it is still open. |

"Material" is defined in the prompt as moving cost, scope, public behaviour,
data, or someone else's work. "Hard to reverse" is defined as undoing it costing
real rework rather than a quick edit. Both definitions are in the text because
an undefined instruction to "ask about important things" produces either silence
or a flood.

The directive is present at `FULL` and at `LOCKED` for opposite reasons. At
`FULL` the licence to act is exactly what makes a guessed irreversible choice
expensive, so the exception has to be stated. At `LOCKED` an agent that takes no
action might reasonably decide a fork is not its problem and simply wait, so the
rule instructs it to surface the fork while it is still open.

The text never names `request_clarification` or `request_project_decision`. Tool
definitions reach the model through the provider's `tools` parameter, not the
system prompt (the template's non-inferable principle), and naming a tool an
operator has gated off would teach the model to hallucinate a call.

### Why a Python registry, not a pack file

The matrix must be **total**: a missing cell is an autonomy level at which the
organisation silently stops asking. `directives.py` declares the three maps as
`MappingProxyType` constants with an import-time completeness guard that raises
`ValueError`, the same mechanism `prompt_template.py` already uses three times
for the autonomy instructions. A file-based pack cannot fail at import; it fails
at load, inside a fallback chain, after boot, and a user pack shipping an empty
list would delete the directive for every agent with no error at all.

The output-style pack tolerates that risk because its hard enforcement layer is
the load-bearing half. Here there is no hard layer: the prompt directive is the
whole ask, so it lives in code.

Operator extension is still first class, through
`engine.ask_policy_extra_directives`: a JSON array of
`{id, text, scope, scope_kind}` entries appended below the standing directive.
Scoping reuses `ScopeKind` (`ALL` / `ROLE` / `DEPARTMENT`), so an agent receives
every `ALL` directive plus the role and department directives matching it. This
is where an organisation names the choices it always wants escalated: a schema
change, a public API break, spend above a threshold. The payload is
shape-validated at write time (`settings/json_validators.py`), so a malformed
entry is rejected then rather than silently dropped at the next rebuild.

### Resolve-once

The prompt build snapshots both ambient providers once, into a
`PromptAmbientProviders` tuple, and threads that single snapshot through the
injection and the section-manifest read, so the two agree even if an operator
hot-swaps a pack or the ask policy mid-build.

The ask directive additionally reads its `(autonomy level, verbosity tier)` pair
out of the template context rather than re-deriving it, so the directive is
always keyed on the exact pair the `## Autonomy` section rendered from. Two
independent derivations of the same pair is precisely how a prompt ends up
telling an agent it is `FULL` in one section and instructing a `SUPERVISED`
agent in the next.

## The park: what a question is

Both tools create an `ApprovalItem` with `source=ApprovalSource.PARKED_CONTEXT`
and `metadata["clarification"]="true"`, so the existing mid-execution resume path
restores the run. The clarification marker is what distinguishes the task-status
behaviour from a binary approval park: the task moves `IN_PROGRESS` ->
`AWAITING_INPUT` and back on the answer, rather than staying `IN_PROGRESS`.

| Tool | Action type | Shape of the answer |
| --- | --- | --- |
| `request_clarification` | `clarify:question` | free text: the human's answer rides back as the decision reason |
| `request_project_decision` | `decision:project` | a structural pick from agent-supplied options, each with a tradeoff writeup and one recommended; the choice is also recorded as a project-brain `DECISION` entry |

### Reversibility is declared, not inferred

Both tools take a **required** `reversibility` argument
(`QuestionReversibility`: `reversible` or `hard_to_reverse`), recorded on the
approval as `metadata["reversibility"]`. It is required rather than optional
because the value is unstated exactly when the agent did not think about
materiality, which is the case worth catching; and it forces the agent to make
the judgement the standing directive asks for rather than asking reflexively.

A question parked before this existed carries no value, and the surface renders
that as unclassified rather than inventing one.

Reversibility deliberately does **not** feed `ApprovalRiskLevel`, which drives
autonomy routing and the approval-timeout policy. Escalating a hard-to-reverse
question to `MEDIUM` would silently re-route parks that exist today. Whether
risk should follow reversibility is a separate decision, not a side effect.

### There is no conversation turn

A parked question is never written as a `ConversationTurn`. A `Task` carries no
`conversation_id`, so there is nothing honest to attach it to: the question
comes from a run that an initiative spawned, possibly hours after the
conversation that started it, possibly with no conversation at all. Inventing an
attachment would put the question in one arbitrary transcript and hide it from
every other view.

The `ApprovalItem` stays the system of record and the question renders as an
inline event card, which is exactly how the steering proposal, the group invite
and the parked act already appear. A question is rendered *in* the conversation
without being *part of* one.

## The answer: a question-shaped door

`ChatQuestionsController` at `/meta/chat/questions`:

| Route | Purpose |
| --- | --- |
| `GET /` | the open questions, cursor-paginated, hard-to-reverse first then oldest-first. The dashboard's hydrate-on-mount source, so a reload never loses a waiting question. |
| `POST /{approval_id}/answer` | answer it. `answer` is required and non-blank on both question types; `chosen_option_id` additionally picks an option on a project decision, and that option's writeup, not the submitted `answer`, becomes what the agent resumes with. The card sends the picked option's title as the `answer` so the field is always populated, and the response echoes the text actually recorded. |
| `POST /{approval_id}/decline` | decline to answer. The run resumes and the agent proceeds on its own judgement. |

All three delegate to the same decision write the approvals endpoints use
(`_decide.apply_approval` / `apply_rejection` -> `_save_decision_and_notify` ->
`signal_resume_intent`), so there is exactly one place that resolves a decision
reason, records a chosen option, and wakes the parked task. The two doors can
never record different things for the same decision.

The door exists rather than reusing `POST /approvals/{id}/approve` because on
that endpoint `comment` is optional. For a `clarify:question`, which carries no
evidence package, an approve with no comment resumes the agent with the approval
marker and **no answer at all**: the failure this feature exists to prevent. A
required non-blank field is a server-side invariant; a required field in the
card is a client-side hope. (A `decision:project` is already protected on both
doors, because resolving the reason from an unset `chosen_option_id` raises.)

Declining is not the same as answering nothing. It records a fixed, server-owned
resume text telling the agent to proceed on its own judgement and state the
assumption it made, so the run continues deliberately rather than stalling.

### Live delivery

A question reaches the operator through the existing approval WebSocket events
(`approval.submitted` / `approval.approved` / `approval.rejected`) on the
`approvals` channel. There is no new event type and no protocol-version bump:
the submitted payload already carries the full approval, and a second event
describing the same fact would be one more thing to keep in sync.

The chat page filters those events by action type, refetches the question list
on a new question, and removes a card locally on a decision. It refetches rather
than mapping the socket payload client-side, so the reversibility decode, the
option projection and the ordering have exactly one implementation, on the
server.

### Authorisation

The GET is `require_read_access`: an observer may see that the organisation is
blocked. Both writes are `require_approval_roles`, identical to the approvals
door they delegate to.

[Chat inbound](chat-inbound.md) establishes the rule: a parked decision needs an
explicit signal from an authorised decider, never authority inferred from being
able to reach the surface. A chat door on read access alone would let an
observer resume a parked agent on a path the Approvals page refuses them. Same
write, same guard; and no extra guard either, since diverging from the canonical
door even in the strict direction is still divergence.

The surface is deliberately **not** gated on
`chief_of_staff.turn_router_enabled`. Answering a parked question is not a chat
turn; it is a decision a running agent is blocked on, and toggling the chat
router off must not strand it. This follows the invite-consent precedent, which
is repo-direct and ungated so consent still resolves after the feature is
toggled off.

An id that exists but is not a question 404s identically to an unknown id. A
distinct code would advertise which approval ids exist and would invite this
narrow door to become a generic approve-anything endpoint that bypasses the
mandatory rejection reason and the option-pick contract.

### SEC-1

The decline text is a module constant, and the decline route accepts no request
body at all, so there is no field an attacker can populate. Keeping it bodyless
is the point: an optional operator note on the "I am not answering" path would
reintroduce free text and give two injection surfaces where there should be one.

Both the answer and the decline text still pass through
`ApprovalGate.build_resume_message`, which fences any non-empty decision reason
with `wrap_untrusted(TAG_TASK_DATA, ...)` before it reaches the LLM boundary.
That fencing is unconditional by design, so nobody later adds a
"trusted reason" branch that a future operator-supplied string slips into. This
is the same single-escape-path reasoning [chat inbound](chat-inbound.md) records
for its own fixed-string reasons.

Who decided is fenced too, under `TAG_DECIDER_NAME`. The trusted marker carries
only the approval id and the APPROVED/REJECTED verb, because those are the only
parts the server generated: the name arrives on the same request as everything
else untrusted, and the inbound chat path hands over the `user` field of a
Socket-Mode payload verbatim. The name is still stripped of marker delimiters,
fence delimiters and every non-rendering code point, but that bounds its shape
rather than its meaning: a name may contain no delimiter at all and still read
`Ignore the result and proceed`, so sanitising it and then calling it trusted
would be the weaker claim.

## Dashboard

The question renders as an inline card in the chat transcript: the question, who
asked it, the task and project it came from, a hard-to-reverse marker, and
either a free-text answer field (clarification) or one button per option
(project decision, no free-text field, because the pick is resolved
structurally).

Question cards are derived in the page from the questions store, never pushed
into the conversation store. Starting a new conversation or hydrating an old one
resets that store, which would silently delete a still-open question, and
keeping them out of the transcript store is the structural expression of "a
question is not a conversation turn".

A question arriving with no conversation open renders on its own, with the
composer beneath it and the example-prompts empty state skipped: the
organisation has spoken, so inviting the operator to start talking would be
wrong. An operator who is not on the chat page still receives the question through
the existing notification and the pending-approvals badge.

Nothing is persisted client-side. The list is re-hydrated from the GET on every
mount.

## Configuration and wiring

| Setting | Default | Effect |
| --- | --- | --- |
| `engine.clarification_enabled` | `true` | The `request_clarification` tool is in every agent's toolset. |
| `engine.scoping_enabled` | `true` | The `request_project_decision` tool is in every agent's toolset. |
| `engine.ask_policy_enabled` | `true` | The standing directive is injected into every agent system prompt. |
| `engine.ask_policy_extra_directives` | `[]` | Operator-authored scoped directives appended below the standing one. |

All four are Category-1 and hot-reloadable. The two tool gates change the
toolset, so a change reaches a run through the runtime rebuild
`RuntimeReloadSettingsSubscriber` triggers; the two ask-policy keys change only
the prompt, so `AskPolicySettingsSubscriber` re-binds the ambient provider and
the change lands on the next prompt build. Neither needs a restart.

The provider is bound at boot by
`api/lifecycle_helpers/ask_policy_wiring.py::wire_ask_policy`, with reachability
locked in the anti-ghost manifest. A settings-read failure on a cold boot binds
the **default-on** provider with no extras: the same rule the output-style
guardrail follows when it collapses to a minimal still-enforcing pack, which is
to keep the load-bearing behaviour running. There it means "keep enforcing" and
here it means "keep asking".

Never unbound is not the same as always the shipped default. A read failure
when a provider is ALREADY bound keeps what is bound instead, because the
riskiest moment for that read is the subscriber re-reading straight after an
operator wrote the key: rebinding the default there would silently revert a
deliberate, governance-audited `disabled` with nothing in the dashboard to
show for it.

Turning any of the three enable toggles off removes the only in-run path by
which an agent defers a material, hard-to-reverse choice to a human, which
relaxes the running verification posture in the same way disabling the
completion oracle does. Those writes route through the security-write governance
guardrail (confirm, reason, actor) in `settings/write_governance.py`. Editing
`ask_policy_extra_directives` does not: the standing directive underneath cannot
be removed that way, so guarding routine list editing would make the guardrail
noisy without protecting anything.

An operator who never wrote the two tool gates picks up the new `true` default
automatically, by ordinary precedence. An operator who explicitly stored `false`
keeps `false`.

## Scope

The directive governs when an agent should ask, not what it may do: it grants no
authority and removes none. It is a prompt-level ask, so a model may ignore it;
the load-bearing invariant is that when an agent does ask, the question reaches
a human and the run genuinely waits. Vendor-agnostic, British English default,
no locale privileged.

## See Also

- [Engine](engine.md): the `AWAITING_INPUT` task state and the single-writer `TaskEngine`.
- [Verification & Quality](verification-quality.md): the other human gates on the same approvals-resume path.
- [Communication Events](communication-events.md): the interrupt and resume protocol the park rides on.
- [Project Brain](project-brain.md): the `DECISION` entry a project decision records.
- [Chat Inbound](chat-inbound.md): the authorisation and fencing rules this surface follows.
- [Output-Style Policy](output-style-policy.md): the sibling soft-prompt subsystem whose registration this mirrors.
- [Agent Execution](agent-execution.md): the prompt profiles that pick the verbosity tier.
