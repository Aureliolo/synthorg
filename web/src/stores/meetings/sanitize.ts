import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import {
  MEETING_PHASE_VALUES,
  MEETING_PROTOCOL_TYPE_VALUES,
  MEETING_STATUS_VALUES,
} from '@/api/types/meetings'
import { PRIORITY_VALUES } from '@/api/types/enums'
import type {
  MeetingAgenda,
  MeetingContribution,
  MeetingMinutes,
  MeetingResponse,
} from '@/api/types/meetings'

// Status, protocol_type, phase, and priority all flow through
// sanitizeWsEnum (see sanitizeMeeting, sanitizeMeetingMinutes,
// sanitizeContribution, sanitizeMinutesCollections), so the shape
// predicates only need to check ``typeof === 'string'``. Unknown
// enum values fall back via the sanitizer rather than collapsing the
// whole frame.

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Validate that a ``token_usage_by_participant`` map is a plain ``Record<string, number>``. */
function isTokenUsageMap(value: unknown): value is Record<string, number> {
  if (!isPlainObject(value)) return false
  for (const [key, count] of Object.entries(value)) {
    // Token counters must be finite non-negative numbers -- a NaN /
    // Infinity / negative value on the wire would poison downstream
    // spend math the moment the store surfaces it.
    if (typeof key !== 'string') return false
    if (typeof count !== 'number' || !Number.isFinite(count) || count < 0) {
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
  if (!isPlainObject(value)) return false
  const v = value
  return (
    typeof v.title === 'string'
    && typeof v.description === 'string'
    && (v.presenter_id === null || typeof v.presenter_id === 'string')
  )
}

const CONTRIBUTION_REQUIRED_STRING_FIELDS = [
  'agent_id',
  'content',
  'timestamp',
] as const

const CONTRIBUTION_REQUIRED_NUMERIC_FIELDS = [
  'turn_number',
  'input_tokens',
  'output_tokens',
] as const

/**
 * Every contribution must carry every field ``sanitizeContribution``
 * persists: the WS-origin strings and the numeric / enum scalars it
 * copies verbatim. Without the enum check an out-of-range ``phase``
 * would reach the UI, and without the finite-number checks ``NaN`` /
 * ``Infinity`` in the token counters could corrupt meeting totals.
 */
function isContributionShape(value: unknown): boolean {
  if (!isPlainObject(value)) return false
  for (const field of CONTRIBUTION_REQUIRED_STRING_FIELDS) {
    if (typeof value[field] !== 'string') return false
  }
  // Don't reject on allowlist miss here: ``sanitizeContribution`` runs
  // ``sanitizeWsEnum`` which emits the structured warning and falls
  // back, so a rolling backend that ships a new phase value doesn't
  // collapse the whole frame.
  if (typeof value.phase !== 'string') return false
  for (const field of CONTRIBUTION_REQUIRED_NUMERIC_FIELDS) {
    if (!isNonNegInt(value[field])) return false
  }
  return true
}

/**
 * Every action item must have ``description`` + nullable
 * ``assignee_id`` + a ``priority`` drawn from the canonical enum.
 */
function isActionItemShape(value: unknown): boolean {
  if (!isPlainObject(value)) return false
  // Same reasoning as ``isContributionShape``: don't reject on
  // allowlist miss here; ``sanitizeMinutesCollections`` runs
  // ``sanitizeWsEnum`` on priority and falls back consistently.
  return (
    typeof value.description === 'string'
    && (value.assignee_id === null || typeof value.assignee_id === 'string')
    && typeof value.priority === 'string'
  )
}

function isMinutesAgendaShape(value: unknown): boolean {
  if (!isPlainObject(value)) return false
  return (
    typeof value.title === 'string'
    && typeof value.context === 'string'
    && Array.isArray(value.items)
    && value.items.every(isAgendaItemShape)
  )
}

const MINUTES_REQUIRED_STRING_FIELDS = [
  'meeting_id',
  'protocol_type',
  'leader_id',
  'summary',
  'started_at',
  'ended_at',
] as const

const MINUTES_REQUIRED_NUMERIC_FIELDS = [
  'total_input_tokens',
  'total_output_tokens',
  'total_tokens',
] as const

function isMinutesRequiredScalars(m: Record<string, unknown>): boolean {
  for (const field of MINUTES_REQUIRED_STRING_FIELDS) {
    if (typeof m[field] !== 'string') return false
  }
  for (const field of MINUTES_REQUIRED_NUMERIC_FIELDS) {
    if (!isNonNegInt(m[field])) return false
  }
  return typeof m.conflicts_detected === 'boolean'
}

function isStringArrayValue(value: unknown): boolean {
  return Array.isArray(value) && value.every((s) => typeof s === 'string')
}

function isShapedArray(
  value: unknown,
  predicate: (entry: unknown) => boolean,
): boolean {
  return Array.isArray(value) && value.every(predicate)
}

function isMinutesCollections(m: Record<string, unknown>): boolean {
  return (
    isStringArrayValue(m.participant_ids)
    && isMinutesAgendaShape(m.agenda)
    && isShapedArray(m.contributions, isContributionShape)
    && isStringArrayValue(m.decisions)
    && isShapedArray(m.action_items, isActionItemShape)
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
  if (value === null || value === undefined) return true
  if (!isPlainObject(value)) return false
  return isMinutesRequiredScalars(value) && isMinutesCollections(value)
}

function isMeetingScalarFields(c: Record<string, unknown>): boolean {
  return (
    typeof c.meeting_id === 'string'
    && typeof c.status === 'string'
    && typeof c.meeting_type_name === 'string'
    && typeof c.protocol_type === 'string'
    && typeof c.token_budget === 'number'
    && Number.isFinite(c.token_budget)
    && c.token_budget >= 0
  )
}

function isMeetingDurationField(value: unknown): boolean {
  if (value === null) return true
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isMeetingNullableFields(c: Record<string, unknown>): boolean {
  return (
    isMeetingMinutesShape(c.minutes)
    && (c.error_message === null || typeof c.error_message === 'string')
    && isMeetingDurationField(c.meeting_duration_seconds)
    && (c.token_usage_by_participant === undefined
      || isTokenUsageMap(c.token_usage_by_participant))
  )
}

/**
 * Type predicate: a WS payload object satisfies the {@link MeetingResponse}
 * shape so consumers can use it without a cast.
 */
export function isMeetingShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & MeetingResponse {
  // Enum fields (status, protocol_type) accept any non-empty string;
  // sanitizeMeeting routes them through sanitizeWsEnum which applies
  // the allowlist + safe fallback. Rejecting unknown values here
  // would drop the whole frame on rolling backend deploys.
  return (
    isMeetingScalarFields(c)
    && isStringArrayValue(c.contribution_rank)
    && isMeetingNullableFields(c)
  )
}

function sanitizeAgenda(agenda: MeetingAgenda): MeetingAgenda {
  return {
    title: sanitizeWsString(agenda.title, 256) ?? '',
    context: sanitizeWsString(agenda.context, 2048) ?? '',
    items: agenda.items.map((item) => ({
      title: sanitizeWsString(item.title, 256) ?? '',
      description: sanitizeWsString(item.description, 1024) ?? '',
      // Nullable: if sanitization blanks a non-null id, collapse to
      // ``null`` rather than emitting ``''`` so the wire contract is
      // preserved.
      presenter_id: item.presenter_id === null
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
    phase: sanitizeWsEnum(
      c.phase,
      MEETING_PHASE_VALUES,
      'discussion',
      { maxLen: 64, field: 'meeting.contribution.phase' },
    ),
    turn_number: c.turn_number,
    input_tokens: c.input_tokens,
    output_tokens: c.output_tokens,
    timestamp: sanitizeWsString(c.timestamp, 64) ?? '',
  }
}

function sanitizeMinutesStrings(minutes: MeetingMinutes) {
  return {
    meeting_id: sanitizeWsString(minutes.meeting_id, 128) ?? '',
    protocol_type: sanitizeWsEnum(
      minutes.protocol_type,
      MEETING_PROTOCOL_TYPE_VALUES,
      'round_robin',
      { maxLen: 64, field: 'meeting.minutes.protocol_type' },
    ),
    leader_id: sanitizeWsString(minutes.leader_id, 128) ?? '',
    summary: sanitizeWsString(minutes.summary, 4096) ?? '',
    started_at: sanitizeWsString(minutes.started_at, 64) ?? '',
    ended_at: sanitizeWsString(minutes.ended_at, 64) ?? '',
  }
}

function sanitizeMinutesCollections(minutes: MeetingMinutes) {
  return {
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
    decisions: minutes.decisions
      .map((d) => sanitizeWsString(d, 1024) ?? '')
      .filter((d) => d.length > 0),
    action_items: minutes.action_items.map((ai) => ({
      description: sanitizeWsString(ai.description, 1024) ?? '',
      assignee_id: ai.assignee_id === null
        ? null
        : sanitizeWsString(ai.assignee_id, 128) || null,
      priority: sanitizeWsEnum(
        ai.priority,
        PRIORITY_VALUES,
        'medium',
        { maxLen: 32, field: 'meeting.action_item.priority' },
      ),
    })),
  }
}

function sanitizeMeetingMinutes(
  minutes: MeetingMinutes | null,
): MeetingMinutes | null {
  if (minutes === null) return null
  return {
    ...sanitizeMinutesStrings(minutes),
    ...sanitizeMinutesCollections(minutes),
    conflicts_detected: minutes.conflicts_detected,
    total_input_tokens: minutes.total_input_tokens,
    total_output_tokens: minutes.total_output_tokens,
    total_tokens: minutes.total_tokens,
  }
}

function sanitizeTokenUsage(
  raw: Record<string, number> | undefined,
): Record<string, number> {
  const tokenUsage: Record<string, number> = {}
  for (const [participantId, count] of Object.entries(raw ?? {})) {
    const safeId = sanitizeWsString(participantId, 128)
    if (safeId && safeId.length > 0) {
      tokenUsage[safeId] = count
    }
  }
  return tokenUsage
}

/**
 * Return a sanitized copy of a ``MeetingResponse`` with every
 * untrusted string field validated by ``isMeetingShape`` routed
 * through ``sanitizeWsString`` so bidi overrides and control chars
 * never reach the rendered UI.
 */
export function sanitizeMeeting(c: MeetingResponse): MeetingResponse {
  return {
    meeting_id: sanitizeWsString(c.meeting_id, 128) ?? '',
    meeting_type_name: sanitizeWsString(c.meeting_type_name, 128) ?? '',
    protocol_type: sanitizeWsEnum(
      c.protocol_type,
      MEETING_PROTOCOL_TYPE_VALUES,
      'round_robin',
      { maxLen: 64, field: 'meeting.protocol_type' },
    ),
    status: sanitizeWsEnum(
      c.status,
      MEETING_STATUS_VALUES,
      'scheduled',
      { maxLen: 64, field: 'meeting.status' },
    ),
    minutes: sanitizeMeetingMinutes(c.minutes ?? null),
    // Preserve the ``string | null`` contract: if sanitization strips
    // a non-null error_message down to empty, report ``null`` rather
    // than an empty string the UI would treat as a real error.
    error_message: c.error_message === null
      ? null
      : sanitizeWsString(c.error_message, 512) || null,
    token_budget: c.token_budget,
    token_usage_by_participant: sanitizeTokenUsage(
      c.token_usage_by_participant,
    ),
    contribution_rank: c.contribution_rank
      .map((agentId) => sanitizeWsString(agentId, 128) ?? '')
      .filter((agentId) => agentId.length > 0),
    meeting_duration_seconds: c.meeting_duration_seconds,
  }
}
