import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { MeetingResponse } from '@/api/types/meetings'
import type { WsEvent } from '@/api/types/websocket'
import { isMeetingShape, sanitizeMeeting } from './sanitize'
import type { MeetingsGet, MeetingsSet, MeetingsState } from './types'

const log = createLogger('meetings')

function upsertMeetingImpl(set: MeetingsSet, meeting: MeetingResponse): void {
  set((s) => {
    const idx = s.meetings.findIndex(
      (m) => m.meeting_id === meeting.meeting_id,
    )
    const newMeetings = idx === -1
      ? [meeting, ...s.meetings]
      : s.meetings.map((m, i) => (i === idx ? meeting : m))
    const selectedMeeting = s.selectedMeeting?.meeting_id === meeting.meeting_id
      ? meeting
      : s.selectedMeeting
    const patch: Partial<MeetingsState> = { meetings: newMeetings, selectedMeeting }
    if (idx === -1) patch.total = s.total + 1
    return patch
  })
}

function isMeetingPayloadObject(payload: Record<string, unknown>): boolean {
  return typeof payload.meeting === 'object'
    && payload.meeting !== null
    && !Array.isArray(payload.meeting)
}

function handleWsEventImpl(get: MeetingsGet, event: WsEvent): void {
  const { payload } = event
  if (!isMeetingPayloadObject(payload)) {
    log.warn('Event has no meeting payload, skipping:', event.event_type)
    return
  }
  const candidate = payload.meeting as Record<string, unknown>
  if (!isMeetingShape(candidate)) {
    log.error('Received malformed meeting WS payload, skipping upsert', {
      meeting_id: sanitizeForLog(candidate.meeting_id),
      hasStatus: typeof candidate.status === 'string',
      hasTypeName: typeof candidate.meeting_type_name === 'string',
      hasTokenBudget: typeof candidate.token_budget === 'number',
    })
    return
  }
  const sanitized = sanitizeMeeting(candidate)
  if (!sanitized.meeting_id) {
    // sanitizeWsString can return '' for a whitespace-only or
    // all-control-char id that isMeetingShape accepted as a
    // string. Upserting under '' would collapse unrelated meetings
    // into the same slot -- skip and log instead.
    log.error(
      'Meeting payload has empty id after sanitization, skipping upsert',
      { meeting_id: sanitizeForLog(candidate.meeting_id) },
    )
    return
  }
  get().upsertMeeting(sanitized)
}

export function createWsHandler(set: MeetingsSet, get: MeetingsGet) {
  return {
    handleWsEvent: (event: WsEvent) => handleWsEventImpl(get, event),
    upsertMeeting: (meeting: MeetingResponse) =>
      upsertMeetingImpl(set, meeting),
  }
}
