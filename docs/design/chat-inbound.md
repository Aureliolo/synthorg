# Chat Inbound: Human Replies Re-Enter Tasks

The agent-facing chat tools (`chat_messages` / `chat_directory`) are
send/read only, so a human reply on the chat platform never re-enters the
task that asked for it. Chat inbound closes that loop: a long-running Slack
Socket-Mode connection consumes mentions, direct messages, thread replies,
and reactions, and routes each one back to the parked approval it answers, so a
conversation completes without the human ever touching the dashboard.

The feature is **off by default** (`tools.chat_inbound_enabled = false`) and
inert until an operator also points `tools.chat_inbound_connection` at a
Slack connection holding a Socket-Mode app-level token (`xapp-...`). It adds
no public ingress: Socket-Mode is an **outbound** WebSocket to Slack.

## Layering

Everything lives under `integrations/chat_api/inbound/`, split so the I/O
sits behind pure, unit-testable seams:

| Module | Responsibility |
| --- | --- |
| `models.py` | `InboundChatEvent` (vendor-neutral event) + its kind |
| `decode.py` | pure Socket-Mode frame -> event (no I/O) |
| `socket_mode.py` | the WebSocket transport (open + stream + ack) |
| `registry.py` | `(channel, thread_ts) -> approval_id` correlation |
| `router.py` | event -> approve/reject decision -> resume dispatch |
| `consumer.py` | the kill-switched long-running loop + reconnect |

The concrete resume dispatcher (`api/chat_inbound_resume.py`) lives in the
api layer because it drives the approval flow; the router depends only on
the `ChatResumeDispatcher` protocol, so the `integrations` package stays
free of any engine/approval import.

## Flow

1. **Correlate on notify.** When an approval escalates to Slack, the
   `SlackNotificationSink` posts the prompt and registers
   `(channel, message_ts) -> approval_id` in the boot-scoped
   `InboundThreadRegistry` (shared with the consumer). The registry is
   bounded transient routing state, not domain state; a still-pending
   approval is re-notified on its own cadence after a restart.
2. **Open + stream.** The consumer resolves the connection's app token,
   calls `apps.connections.open` (host-pinned to slack.com) for a
   short-lived `wss://` gateway URL, and streams frames. An event envelope
   is acknowledged only **after** its event is dispatched: the resume
   dispatcher is idempotent (`save_if_pending` CAS), so at-least-once
   delivery (a re-delivery if the process dies between receipt and ack) is
   strictly safer than losing a human decision. A disconnect envelope is
   still acked before reconnecting.
3. **Decode.** `decode_frame` maps `app_mention` / `message` (including
   direct messages) / `reaction_added` onto `InboundChatEvent`, dropping bot
   echoes and message subtypes (edits/joins). A top-level message roots its
   own thread for correlation.
4. **Route + resume.** The router resolves the event's thread to an
   approval and decides **explicit-token-only** (see Authorization). The
   `ApprovalResumeDispatcher` records the decision atomically
   (`save_if_pending`, so a concurrent dashboard decision or a duplicate
   event cannot double-resume) and hands off to `signal_resume_intent` (the
   same internal entrypoint the dashboard approve/reject endpoint uses),
   which resumes the parked task through the existing routing. Both writes
   are bracketed by the resume-intent outbox (see below) so a process death
   between them cannot strand the parked task.

## Crash recovery: the resume-intent outbox

Deciding an approval is two writes that cannot be merged into one. The
decision lands on the `ApprovalItem`, moving it off `PENDING`; only then
does `signal_resume_intent` wake the parked task. A process death between
them strands that task forever: nothing is `PENDING`, so neither a
redelivered Socket-Mode event nor the dashboard can act on it, and no
sweep notices.

`api/resume_intent_outbox.py` closes that window. Both decision paths (the
dashboard endpoint and this dispatcher) call `record_resume_intent` before
the decision write and `clear_resume_intent` once the resume settles, and
`ResumeIntentDrain` resolves whatever survives at the next startup:

| Approval state at drain | Action |
| --- | --- |
| Absent, or still `PENDING` | Discard the marker: the decision never landed, so the approval is still decidable and re-dispatching would invent consent |
| Decided BEFORE the marker was recorded | Discard: the marker was written by a caller that went on to lose `save_if_pending`, so the resume behind it already completed |
| `APPROVED` / `REJECTED`, decided at or after the marker | Re-dispatch `signal_resume_intent`, then clear |
| Re-dispatch raises | **Keep** the marker: the task is still parked, so the next startup retries |

Three properties make that table safe under a race, where one caller wins
`save_if_pending` and the other does not:

- **Recording is insert-if-absent**, never an insert-or-replace, so the
  earliest marker (the one that actually brackets the winner's decision)
  keeps its timestamp and the loser cannot mask it with a later one.
- **Only the winner clears.** A loser returning 409 leaves the marker
  alone, because a concurrent winner may own an in-flight resume behind
  it; the recorded-after-decision row above is what retires a marker no
  one owns.
- **The marker holds only `approval_id` and `recorded_at`**, never a copy
  of the decision. The approval row stays the system of record, so the
  drain reads `status` / `decided_by` / `decision_reason` from there and
  the two can never disagree.

Recording is best-effort (a `None` backend, or an outbox fault, degrades
to a logged no-op) because a run with no database cannot survive a crash
anyway, and forfeiting recovery for one decision beats 500-ing a
serviceable approval.

## Authorization: explicit-token-only

An approval is consent, so it is **never** inferred from arbitrary human
text. Only an explicit approve/reject **reaction** (`white_check_mark` /
`x` ...) constitutes a decision; a plain text reply (mention / message /
DM) resolves to no decision and is ignored. This closes the gap where any
user who could post in the notified thread would approve a parked action
(including a destructive one) merely by typing something: the Socket-Mode
path now requires the same explicit affirmative signal the dashboard does,
not a free-text reply.

## Kill-switch + resilience

The consumer is a resident `start()`/`stop()` service wired like
`WebhookEventBridge` (constructed at boot, started in the on-startup runner,
drained at shutdown). Its loop reads `tools.chat_inbound_enabled` live per
iteration and **fail-safes to DISABLED**: an inbound control surface must
never self-enable on a settings outage. Reconnect-on-drop is delegated to
the shared `GeneralRetryHandler` (never a hand-rolled backoff); a single
malformed event is isolated so it cannot drop the socket.

## SEC-1: fencing

Inbound human content is attacker-controlled. The inbound package never
turns it into a prompt: the router forwards a decision's reason **only** as
a resume `decision_reason`, which `build_resume_message` fences with
`wrap_untrusted(TAG_TASK_DATA, ...)` before any LLM boundary: the exact
path the dashboard approval comment takes, so there is one fencing site,
not two. (Under explicit-token-only the reason is a fixed string rather
than raw human text, but the fenced hand-off remains the single escape
path.) The `check_chat_inbound_fenced.py` gate enforces this structurally:
no inbound module may call an LLM-completion chokepoint, and
`InboundResumeRouter.route` must keep the `decision_reason=` keyword on its
own reachable `self._dispatcher.resume(...)` call. Binding the check to that
one call is what makes it load-bearing: a `decision_reason=` sitting on a
helper, on a different collaborator, or after an early return would satisfy
a module-wide search while the real hand-off shipped the text unfenced.
