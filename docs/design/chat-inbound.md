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
   short-lived `wss://` gateway URL, and streams frames. Every envelope is
   acknowledged before its event is dispatched so a slow handler cannot
   trigger re-delivery.
3. **Decode.** `decode_frame` maps `app_mention` / `message` (including
   direct messages) / `reaction_added` onto `InboundChatEvent`, dropping bot
   echoes and message subtypes (edits/joins). A top-level message roots its
   own thread for correlation.
4. **Route + resume.** The router resolves the event's thread to an
   approval. A reaction (`white_check_mark` / `x` ...) is an explicit
   approve/reject; a text reply is an approving reply whose body becomes
   the human guidance. The `ApprovalResumeDispatcher` records the decision
   atomically (`save_if_pending`, so a concurrent dashboard decision or a
   duplicate event cannot double-resume) and hands off to
   `signal_resume_intent` (the same internal entrypoint the dashboard
   approve/reject endpoint uses), which resumes the parked task through
   the existing routing.

## Kill-switch + resilience

The consumer is a resident `start()`/`stop()` service wired like
`WebhookEventBridge` (constructed at boot, started in the on-startup runner,
drained at shutdown). Its loop reads `tools.chat_inbound_enabled` live per
iteration and **fail-safes to DISABLED**: an inbound control surface must
never self-enable on a settings outage. Reconnect-on-drop is delegated to
the shared `GeneralRetryHandler` (never a hand-rolled backoff); a single
malformed event is isolated so it cannot drop the socket.

## SEC-1: fencing

Inbound human text is attacker-controlled. The inbound package never turns
it into a prompt: the router forwards it **only** as a resume
`decision_reason`, which `build_resume_message` fences with
`wrap_untrusted(TAG_TASK_DATA, ...)` before any LLM boundary: the exact
path the dashboard approval comment takes, so there is one fencing site,
not two. The `check_chat_inbound_fenced.py` gate enforces this structurally:
no inbound module may call an LLM-completion chokepoint, and the router must
keep the `decision_reason=` hand-off.
