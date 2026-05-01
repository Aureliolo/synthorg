import { create } from 'zustand'
import * as meetingsApi from '@/api/endpoints/meetings'
import {
  MEETING_PHASE_VALUES,
  MEETING_PROTOCOL_TYPE_VALUES,
  MEETING_STATUS_VALUES,
} from '@/api/types/meetings'
import { PRIORITY_VALUES } from '@/api/types/enums'
import { sanitizeWsString } from '@/stores/notifications'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  MeetingAgenda,
  MeetingContribution,
  MeetingFilters,
  MeetingMinutes,
  MeetingResponse,
  TriggerMeetingRequest,
} from '@/api/types/meetings'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('meetings')

// Runtime sets derived from the canonical enum tuples -- any drift
// between validator and union is caught at compile time.
const MEETING_STATUS_SET: ReadonlySet<string> = new Set<string>(MEETING_STATUS_VALUES)
const MEETING_PROTOCOL_TYPE_SET: ReadonlySet<string> = new Set<string>(MEETING_PROTOCOL_TYPE_VALUES)
const PRIORITY_SET: ReadonlySet<string> = new Set<string>(PRIORITY_VALUES)
const MEETING_PHASE_SET: ReadonlySet<string> = new Set<string>(MEETING_PHASE_VALUES)

/** Validate that a ``token_usage_by_participant`` map is a plain ``Record<string, number>``. */
function isTokenUsageMap(value: unknown): value is Record<string, number> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  for (const [key, count] of Object.entries(value)) {
    // Token counters must be finite non-negative numbers -- a NaN /
    // Infinity / negative value on the wire would poison downstream
    // spend math the moment the store surfaces it.
    if (typeof key !== 'string' || !Number.isFinite(count) || count < 0) {
      return false
    }
  }
  return true
}

/**
 * Finite non-negative integer predicate. Token counters, turn
 * numbers, and meeting totals all share this constraint: NaN,
 * Infinity, negative, or fractional values are rejected so
 * downstream spend/ordering math can't be poisoned by a malformed
 * WS frame.
 */
function isNonNegInt(n: unknown): n is number {
  return typeof n === 'number' && Number.isInteger(n) && n >= 0
}

/** Every agenda item must have the fields ``sanitizeAgenda`` reads. */
function isAgendaItemShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.title === 'string' &&
    typeof v.description === 'string' &&
    (v.presenter_id === null || typeof v.presenter_id === 'string')
  )
}

/**
 * Every contribution must carry every field ``sanitizeContribution``
 * persists: the WS-origin strings and the numeric / enum scalars it
 * copies verbatim. Without the enum check an out-of-range ``phase``
 * would reach the UI, and without the finite-number checks ``NaN`` /
 * ``Infinity`` in the token counters could corrupt meeting totals.
 */
function isContributionShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  // Counters must be non-negative integers: a negative or fractional
  // ``turn_number`` would corrupt contribution ordering, and negative
  // token counts would poison the meeting totals the UI surfaces.
  return (
    typeof v.agent_id === 'string' &&
    typeof v.content === 'string' &&
    typeof v.phase === 'string' &&
    MEETING_PHASE_SET.has(v.phase) &&
    isNonNegInt(v.turn_number) &&
    isNonNegInt(v.input_tokens) &&
    isNonNegInt(v.output_tokens) &&
    typeof v.timestamp === 'string'
  )
}

/**
 * Every action item must have ``description`` + nullable
 * ``assignee_id`` + a ``priority`` drawn from the canonical enum.
 */
function isActionItemShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.description === 'string' &&
    (v.assignee_id === null || typeof v.assignee_id === 'string') &&
    typeof v.priority === 'string' &&
    PRIORITY_SET.has(v.priority)
  )
}

/**
 * Structural check for the nested ``MeetingMinutes`` payload the
 * server emits on ``completed`` meetings. Accepts ``null`` (meeting
 * still in-progress or failed) and otherwise verifies each field
 * the sanitizer dereferences. Element-level guards on the array
 * fields are critical: a malformed frame like ``contributions: [null]``
 * or ``action_items: [{}]`` would previously pass the outer
 * ``Array.isArray`` check and then throw inside
 * ``sanitizeMeetingMinutes`` when it tried to read ``.agent_id`` /
 * ``.description`` on the missing element.
 */
function isMeetingMinutesShape(value: unknown): boolean {
  if (value === null) return true
  if (typeof value !== 'object' || Array.isArray(value)) return false
  const m = value as Record<string, unknown>
  if (typeof m.meeting_id !== 'string') return false
  if (typeof m.protocol_type !== 'string' || !MEETING_PROTOCOL_TYPE_SET.has(m.protocol_type)) {
    return false
  }
  if (typeof m.leader_id !== 'string') return false
  if (
    !Array.isArray(m.participant_ids) ||
    !m.participant_ids.every((id) => typeof id === 'string')
  ) {
    return false
  }
  if (typeof m.agenda !== 'object' || m.agenda === null || Array.isArray(m.agenda)) {
    return false
  }
  const agenda = m.agenda as Record<string, unknown>
  if (typeof agenda.title !== 'string') return false
  if (typeof agenda.context !== 'string') return false
  if (!Array.isArray(agenda.items) || !agenda.items.every(isAgendaItemShape)) {
    return false
  }
  if (
    !Array.isArray(m.contributions) ||
    !m.contributions.every(isContributionShape)
  ) {
    return false
  }
  if (typeof m.summary !== 'string') return false
  if (
    !Array.isArray(m.decisions) ||
    !m.decisions.every((d) => typeof d === 'string')
  ) {
    return false
  }
  if (
    !Array.isArray(m.action_items) ||
    !m.action_items.every(isActionItemShape)
  ) {
    return false
  }
  if (typeof m.conflicts_detected !== 'boolean') return false
  if (!isNonNegInt(m.total_input_tokens)) return false
  if (!isNonNegInt(m.total_output_tokens)) return false
  if (!isNonNegInt(m.total_tokens)) return false
  if (typeof m.started_at !== 'string') return false
  if (typeof m.ended_at !== 'string') return false
  return true
}

/**
 * Type predicate: a WS payload object satisfies the {@link MeetingResponse}
 * shape so consumers can use it without a cast. ``contribution_rank``
 * must be a plain string array of agent ids (matching the declared
 * ``readonly string[]``) and ``token_usage_by_participant`` must be a
 * plain ``Record<string, number>`` -- accepting non-string ranks or
 * array-shaped usage maps would let malformed payloads smuggle bad
 * values into the store.
 */
function isMeetingShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & MeetingResponse {
  return (
    typeof c.meeting_id === 'string' &&
    typeof c.status === 'string' &&
    MEETING_STATUS_SET.has(c.status) &&
    typeof c.meeting_type_name === 'string' &&
    typeof c.protocol_type === 'string' &&
    MEETING_PROTOCOL_TYPE_SET.has(c.protocol_type) &&
    // Budget must be finite + non-negative; NaN/Infinity/negatives
    // would break spend math downstream.
    Number.isFinite(c.token_budget) &&
    typeof c.token_budget === 'number' &&
    c.token_budget >= 0 &&
    Array.isArray(c.contribution_rank) &&
    c.contribution_rank.every((entry) => typeof entry === 'string') &&
    isTokenUsageMap(c.token_usage_by_participant) &&
    // Remaining non-optional ``MeetingResponse`` fields. ``minutes``
    // and ``error_message`` are nullable on the wire (completed
    // meetings fill in ``minutes``; failed meetings fill in
    // ``error_message``). ``meeting_duration_seconds`` is null while
    // in-progress and becomes a finite non-negative number once
    // ended. Accepting null for all three matches the declared types
    // and keeps the guard aligned with the asserted ``MeetingResponse``
    // shape.
    isMeetingMinutesShape(c.minutes) &&
    (c.error_message === null || typeof c.error_message === 'string') &&
    (c.meeting_duration_seconds === null ||
      (typeof c.meeting_duration_seconds === 'number' &&
        Number.isFinite(c.meeting_duration_seconds) &&
        c.meeting_duration_seconds >= 0))
  )
}

/**
 * Return a sanitized copy of a ``MeetingResponse`` with every
 * untrusted string field validated by ``isMeetingShape`` routed
 * through ``sanitizeWsString`` so bidi overrides and control chars
 * never reach the rendered UI. This covers every WS-origin string
 * the store persists: the identifier (``meeting_id``), the enum-
 * typed display strings, the nullable ``error_message``, the
 * ``contribution_rank`` agent ids, and the participant-id keys of
 * ``token_usage_by_participant``.
 */
function sanitizeAgenda(agenda: MeetingAgenda): MeetingAgenda {
  return {
    title: sanitizeWsString(agenda.title, 256) ?? '',
    context: sanitizeWsString(agenda.context, 2048) ?? '',
    items: agenda.items.map((item) => ({
      title: sanitizeWsString(item.title, 256) ?? '',
      description: sanitizeWsString(item.description, 1024) ?? '',
      // ``presenter_id`` is ``string | null`` on the wire. If
      // sanitization blanks a non-null id (bidi-only, control-only),
      // collapse to ``null`` rather than emitting ``''`` so the
      // nullable contract is preserved.
      presenter_id:
        item.presenter_id === null
          ? null
          : sanitizeWsString(item.presenter_id, 128) || null,
    })),
  }
}

function sanitizeContribution(c: MeetingContribution): MeetingContribution {
  // Rebuild explicitly rather than spreading ``...c``: a spread would
  // preserve any unvetted enumerable props that happen to ride along
  // on the WS payload (attacker-reachable), even though the type
  // system believes they cannot exist.
  return {
    agent_id: sanitizeWsString(c.agent_id, 128) ?? '',
    content: sanitizeWsString(c.content, 4096) ?? '',
    phase: c.phase,
    turn_number: c.turn_number,
    input_tokens: c.input_tokens,
    output_tokens: c.output_tokens,
    timestamp: sanitizeWsString(c.timestamp, 64) ?? '',
  }
}

function sanitizeMeetingMinutes(
  minutes: MeetingMinutes | null,
): MeetingMinutes | null {
  if (minutes === null) return null
  // No spread: list every allowed MeetingMinutes field explicitly so
  // any future string key added on the wire but missing from this
  // construction is dropped rather than silently persisted raw.
  return {
    meeting_id: sanitizeWsString(minutes.meeting_id, 128) ?? '',
    protocol_type:
      (sanitizeWsString(minutes.protocol_type, 64) ?? '') as MeetingMinutes['protocol_type'],
    leader_id: sanitizeWsString(minutes.leader_id, 128) ?? '',
    participant_ids: minutes.participant_ids
      .map((id) => sanitizeWsString(id, 128) ?? '')
      .filter((id) => id.length > 0),
    agenda: sanitizeAgenda(minutes.agenda),
    // Drop contributions whose agent_id sanitizes to empty -- same
    // defensive filter we apply to ``participant_ids`` and
    // ``contribution_rank`` so an unrenderable row can't slip through.
    contributions: minutes.contributions
      .map(sanitizeContribution)
      .filter((contribution) => contribution.agent_id.length > 0),
    summary: sanitizeWsString(minutes.summary, 4096) ?? '',
    decisions: minutes.decisions
      .map((d) => sanitizeWsString(d, 1024) ?? '')
      .filter((d) => d.length > 0),
    action_items: minutes.action_items.map((ai) => ({
      description: sanitizeWsString(ai.description, 1024) ?? '',
      // Nullable field: if sanitization blanks a non-null assignee,
      // fall back to ``null`` rather than ``''`` to preserve the
      // wire contract.
      assignee_id:
        ai.assignee_id === null
          ? null
          : sanitizeWsString(ai.assignee_id, 128) || null,
      priority: ai.priority,
    })),
    conflicts_detected: minutes.conflicts_detected,
    total_input_tokens: minutes.total_input_tokens,
    total_output_tokens: minutes.total_output_tokens,
    total_tokens: minutes.total_tokens,
    started_at: sanitizeWsString(minutes.started_at, 64) ?? '',
    ended_at: sanitizeWsString(minutes.ended_at, 64) ?? '',
  }
}

function sanitizeMeeting(c: MeetingResponse): MeetingResponse {
  const tokenUsage: Record<string, number> = {}
  for (const [participantId, count] of Object.entries(c.token_usage_by_participant)) {
    const safeId = sanitizeWsString(participantId, 128)
    if (safeId && safeId.length > 0) {
      tokenUsage[safeId] = count
    }
  }
  // No spread: list every allowed MeetingResponse field so unknown
  // wire props cannot reach the store.
  return {
    meeting_id: sanitizeWsString(c.meeting_id, 128) ?? '',
    meeting_type_name: sanitizeWsString(c.meeting_type_name, 128) ?? '',
    protocol_type: (sanitizeWsString(c.protocol_type, 64) ?? '') as MeetingResponse['protocol_type'],
    status: (sanitizeWsString(c.status, 64) ?? '') as MeetingResponse['status'],
    minutes: sanitizeMeetingMinutes(c.minutes),
    // Preserve the ``string | null`` contract: if sanitization strips
    // a non-null error_message down to empty, report ``null`` rather
    // than an empty string the UI would treat as a real error.
    error_message:
      c.error_message === null
        ? null
        : sanitizeWsString(c.error_message, 512) || null,
    token_budget: c.token_budget,
    token_usage_by_participant: tokenUsage,
    contribution_rank: c.contribution_rank
      .map((agentId) => sanitizeWsString(agentId, 128) ?? '')
      .filter((agentId) => agentId.length > 0),
    meeting_duration_seconds: c.meeting_duration_seconds,
  }
}

export interface MeetingsState {
  // Data
  meetings: MeetingResponse[]
  selectedMeeting: MeetingResponse | null
  total: number

  // Loading
  loading: boolean
  loadingDetail: boolean
  error: string | null
  detailError: string | null

  // Trigger
  triggering: boolean

  // Actions
  fetchMeetings: (filters?: MeetingFilters) => Promise<void>
  fetchMeeting: (meetingId: string) => Promise<void>
  triggerMeeting: (data: TriggerMeetingRequest) => Promise<MeetingResponse[]>

  // Real-time
  handleWsEvent: (event: WsEvent) => void
  upsertMeeting: (meeting: MeetingResponse) => void

  // Lifecycle (#1600 Phase 5). Reserved for teardown of timers /
  // listeners; today this store schedules no async resources, so
  // ``dispose`` is a no-op. The afterEach in ``web/src/test-setup.tsx``
  // calls it so the contract is uniform across domain stores and a
  // future addition (e.g. a ``setInterval``-driven poller) can be
  // plumbed through this method without changing every test file.
  dispose: () => void
}

let listRequestSeq = 0
let detailRequestSeq = 0
// Generation token bumped by ``dispose()``. The seq counters reset
// to zero on dispose so a new fetch starts fresh, but a still-in-flight
// request from before the dispose could otherwise win the
// ``seq === current`` check by collision (its captured seq could
// match a fresh post-dispose seq value). Pairing seq with the epoch
// makes that scenario impossible: a stale request always sees a
// different ``epoch`` and short-circuits.
let requestEpoch = 0

/** Reset module-level request seq counters -- test-only. */
export function _resetRequestSeqs(): void {
  listRequestSeq = 0
  detailRequestSeq = 0
}

export const useMeetingsStore = create<MeetingsState>()((set, get) => ({
  meetings: [],
  selectedMeeting: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  detailError: null,
  triggering: false,

  fetchMeetings: async (filters) => {
    const epoch = requestEpoch
    const seq = ++listRequestSeq
    set({ loading: true, error: null })
    try {
      const result = await meetingsApi.listMeetings(filters)
      if (epoch !== requestEpoch || seq !== listRequestSeq) return // stale
      // Sync selectedMeeting with fresh data
      const currentSelected = get().selectedMeeting
      const freshSelected = currentSelected
        ? result.data.find((m) => m.meeting_id === currentSelected.meeting_id) ?? currentSelected
        : null
      set({
        meetings: result.data,
        total: result.data.length,
        loading: false,
        selectedMeeting: freshSelected,
      })
    } catch (err) {
      if (epoch !== requestEpoch || seq !== listRequestSeq) {
        log.warn('Discarding error from stale list request:', getErrorMessage(err))
        return
      }
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  fetchMeeting: async (meetingId) => {
    const epoch = requestEpoch
    const seq = ++detailRequestSeq
    const current = get().selectedMeeting
    set({
      loadingDetail: true,
      detailError: null,
      selectedMeeting: current?.meeting_id === meetingId ? current : null,
    })
    try {
      const meeting = await meetingsApi.getMeeting(meetingId)
      if (epoch !== requestEpoch || seq !== detailRequestSeq) return // stale
      set({ selectedMeeting: meeting, loadingDetail: false, detailError: null })
    } catch (err) {
      if (epoch !== requestEpoch || seq !== detailRequestSeq) {
        log.warn('Discarding error from stale detail request:', getErrorMessage(err))
        return
      }
      set({ loadingDetail: false, detailError: getErrorMessage(err) })
    }
  },

  triggerMeeting: async (data) => {
    // Canonical store mutation contract: on failure, log + toast +
    // return sentinel (empty array here -- semantically "no meetings
    // were triggered") so callers never need try/catch. The dialog
    // closes on success (non-empty result) and stays open on failure
    // (empty result), consistent with the ConfirmDialog
    // boolean/undefined-closes / false-stays-open convention.
    set({ triggering: true })
    try {
      const meetings = await meetingsApi.triggerMeeting(data)
      set((s) => ({
        triggering: false,
        meetings: [...meetings, ...s.meetings],
        total: s.total + meetings.length,
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Triggered ${meetings.length} meeting(s)`,
      })
      return meetings
    } catch (err) {
      log.error('triggerMeeting failed:', getErrorMessage(err))
      set({ triggering: false })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not trigger meeting',
        description: getErrorMessage(err),
      })
      return []
    }
  },

  handleWsEvent: (event) => {
    const { payload } = event
    if (!payload.meeting || typeof payload.meeting !== 'object' || Array.isArray(payload.meeting)) {
      log.warn('Event has no meeting payload, skipping:', event.event_type)
      return
    }
    const candidate = payload.meeting as Record<string, unknown>
    if (isMeetingShape(candidate)) {
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
    } else {
      log.error('Received malformed meeting WS payload, skipping upsert', {
        meeting_id: sanitizeForLog(candidate.meeting_id),
        hasStatus: typeof candidate.status === 'string',
        hasTypeName: typeof candidate.meeting_type_name === 'string',
        hasTokenBudget: typeof candidate.token_budget === 'number',
      })
    }
  },

  upsertMeeting: (meeting) => {
    set((s) => {
      const idx = s.meetings.findIndex((m) => m.meeting_id === meeting.meeting_id)
      const newMeetings = idx === -1
        ? [meeting, ...s.meetings]
        : s.meetings.map((m, i) => (i === idx ? meeting : m))
      const selectedMeeting = s.selectedMeeting?.meeting_id === meeting.meeting_id
        ? meeting
        : s.selectedMeeting
      return {
        meetings: newMeetings,
        selectedMeeting,
        ...(idx === -1 ? { total: s.total + 1 } : {}),
      }
    })
  },
  dispose: () => {
    // Bump the generation token so any in-flight request from
    // before the dispose can never collide with post-dispose seq
    // values (the captured ``epoch`` will not match the new
    // ``requestEpoch``). Resetting the seq counters keeps fresh
    // calls starting from zero (#1600 Phase 5).
    requestEpoch += 1
    _resetRequestSeqs()
  },
}))
