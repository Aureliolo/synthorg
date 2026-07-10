/**
 * AG-UI stream event vocabulary (frontend mirror).
 *
 * The per-task progress stream (`GET /api/v1/events/stream?session_id=<taskId>`)
 * emits AG-UI `StreamEvent` frames whose SSE `event:` name is the event type
 * and whose `data:` is the JSON-serialised event. These types are NOT part of
 * the generated DTO surface (the endpoint returns an SSE stream, not a JSON
 * body), so they are hand-authored here and kept in sync with
 * `communication/event_stream/types.py`.
 */

import { sanitizeWsString } from '@/utils/ws-sanitize'

/** AG-UI event types, mirroring `AgUiEventType` on the backend. */
export const AguiEventType = {
  RunStarted: 'run_started',
  RunFinished: 'run_finished',
  RunError: 'run_error',
  StepStarted: 'step_started',
  StepFinished: 'step_finished',
  StepFailed: 'step_failed',
  TextMessageStart: 'text_message_start',
  TextMessageContent: 'text_message_content',
  TextMessageEnd: 'text_message_end',
  ToolCallStart: 'tool_call_start',
  ToolCallArgs: 'tool_call_args',
  ToolCallEnd: 'tool_call_end',
  ApprovalInterrupt: 'approval_interrupt',
  ApprovalResumed: 'approval_resumed',
  InfoRequestInterrupt: 'info_request_interrupt',
  InfoRequestResumed: 'info_request_resumed',
  Dissent: 'synthorg:dissent',
} as const

export type AguiEventType = (typeof AguiEventType)[keyof typeof AguiEventType]

const AGUI_EVENT_TYPES: ReadonlySet<AguiEventType> = new Set(Object.values(AguiEventType))

/** Type guard narrowing an arbitrary string to a known AG-UI event type. */
function isAguiEventType(value: string): value is AguiEventType {
  return (AGUI_EVENT_TYPES as ReadonlySet<string>).has(value)
}

/** The subset of event types the progress surface subscribes to + renders. */
export const AGUI_PROGRESS_EVENTS: readonly AguiEventType[] = [
  AguiEventType.RunStarted,
  AguiEventType.RunFinished,
  AguiEventType.RunError,
  AguiEventType.StepStarted,
  AguiEventType.StepFinished,
  AguiEventType.StepFailed,
  AguiEventType.ToolCallStart,
  AguiEventType.ApprovalInterrupt,
  AguiEventType.ApprovalResumed,
]

/** One parsed AG-UI stream event, with untrusted strings already sanitised. */
export interface AguiStreamEvent {
  id: string
  type: AguiEventType
  sessionId: string
  agentId: string | null
  payload: Record<string, unknown>
}

/** Cap on a payload string-array length so a crafted frame cannot bloat state. */
const MAX_TOOLS_PER_EVENT = 32
/** Cap on a single sanitised label so a crafted frame cannot bloat the UI. */
const MAX_LABEL_LENGTH = 200

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const out: string[] = []
  for (const v of value.slice(0, MAX_TOOLS_PER_EVENT)) {
    if (typeof v !== 'string') continue
    const sanitized = sanitizeWsString(v, MAX_LABEL_LENGTH)
    if (sanitized) out.push(sanitized)
  }
  return out
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** Sanitise a string field, falling back to `fallback` for a non-string. */
function cleanString(value: unknown): string | null {
  return typeof value === 'string' ? (sanitizeWsString(value, MAX_LABEL_LENGTH) ?? null) : null
}

function buildPayload(raw: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = { ...raw }
  if ('tools' in raw) payload['tools'] = toStringArray(raw['tools'])
  return payload
}

/**
 * Parse + sanitise one raw AG-UI frame object into an `AguiStreamEvent`.
 *
 * Returns `null` for a malformed frame or an unknown event type. Untrusted
 * strings (`tools`, `agent_id`) are routed through `sanitizeWsString`; the
 * `tools` array is re-materialised as a sanitised list on the payload so
 * consumers never touch the raw values.
 */
export function parseAguiEvent(raw: unknown): AguiStreamEvent | null {
  if (!isPlainObject(raw)) return null
  const type = raw['type']
  if (typeof type !== 'string' || !isAguiEventType(type)) return null
  return {
    id: cleanString(raw['id']) ?? '',
    type,
    sessionId: cleanString(raw['session_id']) ?? '',
    agentId: cleanString(raw['agent_id']),
    payload: buildPayload(isPlainObject(raw['payload']) ? raw['payload'] : {}),
  }
}
