import { describe, expect, it } from 'vitest'
import {
  computeTokenUsagePercent,
  countByStatus,
  filterMeetings,
  formatMeetingDuration,
  getMeetingStatusColor,
  getMeetingStatusLabel,
  getParticipantTokenShare,
  getPhaseColor,
  getPhaseLabel,
  getProtocolLabel,
  totalTokensUsed,
} from '@/utils/meetings'
import type { MeetingPhase, MeetingProtocolType, MeetingStatus } from '@/api/types/meetings'
import { makeMeeting } from '@/__tests__/helpers/factories'

// -- Meeting status color mapping -------------------------------------------

describe('getMeetingStatusColor', () => {
  it.each<[MeetingStatus, string]>([
    ['scheduled', 'accent'],
    ['in_progress', 'warning'],
    ['completed', 'success'],
    ['failed', 'danger'],
    ['cancelled', 'text-secondary'],
    ['budget_exhausted', 'danger'],
  ])('maps %s to %s', (status, expected) => {
    expect(getMeetingStatusColor(status)).toBe(expected)
  })
})

// -- Meeting status labels --------------------------------------------------

describe('getMeetingStatusLabel', () => {
  it.each<[MeetingStatus, string]>([
    ['scheduled', 'Scheduled'],
    ['in_progress', 'In Progress'],
    ['completed', 'Completed'],
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled'],
    ['budget_exhausted', 'Budget Exhausted'],
  ])('maps %s to %s', (status, expected) => {
    expect(getMeetingStatusLabel(status)).toBe(expected)
  })
})

// -- Protocol labels --------------------------------------------------------

describe('getProtocolLabel', () => {
  it.each<[MeetingProtocolType, string]>([
    ['round_robin', 'Round Robin'],
    ['position_papers', 'Position Papers'],
    ['structured_phases', 'Structured Phases'],
  ])('maps %s to %s', (protocol, expected) => {
    expect(getProtocolLabel(protocol)).toBe(expected)
  })
})

// -- Phase labels -----------------------------------------------------------

describe('getPhaseLabel', () => {
  it.each<[MeetingPhase, string]>([
    ['agenda_broadcast', 'Agenda Broadcast'],
    ['round_robin_turn', 'Round Robin Turn'],
    ['position_paper', 'Position Paper'],
    ['input_gathering', 'Input Gathering'],
    ['discussion', 'Discussion'],
    ['synthesis', 'Synthesis'],
    ['summary', 'Summary'],
  ])('maps %s to %s', (phase, expected) => {
    expect(getPhaseLabel(phase)).toBe(expected)
  })
})

// -- Phase colors -----------------------------------------------------------

describe('getPhaseColor', () => {
  it.each<[MeetingPhase, string]>([
    ['agenda_broadcast', 'accent'],
    ['round_robin_turn', 'accent'],
    ['position_paper', 'accent'],
    ['input_gathering', 'accent'],
    ['discussion', 'warning'],
    ['synthesis', 'success'],
    ['summary', 'success'],
  ])('maps %s to %s', (phase, expected) => {
    expect(getPhaseColor(phase)).toBe(expected)
  })
})

// -- Token usage percent ----------------------------------------------------

describe('computeTokenUsagePercent', () => {
  it('computes percent when minutes and budget exist', () => {
    const meeting = makeMeeting('1', { token_budget: 2000 })
    // total_tokens = 650, budget = 2000 -> 32.5%
    expect(computeTokenUsagePercent(meeting)).toBe(32.5)
  })

  it('returns 0 when minutes is null', () => {
    const meeting = makeMeeting('1', { minutes: null })
    expect(computeTokenUsagePercent(meeting)).toBe(0)
  })

  it('returns 0 when token_budget is 0', () => {
    const meeting = makeMeeting('1', { token_budget: 0 })
    expect(computeTokenUsagePercent(meeting)).toBe(0)
  })

  it('caps at 100', () => {
    const meeting = makeMeeting('1', { token_budget: 100 })
    // total_tokens = 650 > 100
    expect(computeTokenUsagePercent(meeting)).toBe(100)
  })
})

// -- Participant token share ------------------------------------------------

describe('getParticipantTokenShare', () => {
  it('computes share for known participant', () => {
    const meeting = makeMeeting('1')
    // agent-alice: 350 / 650 total = ~53.85%
    const share = getParticipantTokenShare(meeting, 'agent-alice')
    expect(share).toBeCloseTo(53.85, 1)
  })

  it('returns 0 for unknown participant', () => {
    const meeting = makeMeeting('1')
    expect(getParticipantTokenShare(meeting, 'agent-unknown')).toBe(0)
  })

  it('returns 0 when minutes is null', () => {
    const meeting = makeMeeting('1', { minutes: null })
    expect(getParticipantTokenShare(meeting, 'agent-alice')).toBe(0)
  })

  it('returns 0 when total_tokens is 0', () => {
    const base = makeMeeting('1')
    const meeting = makeMeeting('1', {
      minutes: { ...base.minutes!, total_tokens: 0 },
    })
    expect(getParticipantTokenShare(meeting, 'agent-alice')).toBe(0)
  })
})

// -- Duration formatting ----------------------------------------------------

describe('formatMeetingDuration', () => {
  it('returns "--" for null', () => {
    expect(formatMeetingDuration(null)).toBe('--')
  })

  it('returns "--" for negative', () => {
    expect(formatMeetingDuration(-10)).toBe('--')
  })

  it('formats seconds using formatUptime', () => {
    expect(formatMeetingDuration(300)).toBe('5m')
    expect(formatMeetingDuration(3661)).toBe('1h 1m')
  })

  it('returns "0m" for 0 seconds', () => {
    expect(formatMeetingDuration(0)).toBe('0m')
  })

  it('returns "--" for NaN', () => {
    expect(formatMeetingDuration(NaN)).toBe('--')
  })

  it('returns "--" for Infinity', () => {
    expect(formatMeetingDuration(Infinity)).toBe('--')
  })
})

// -- Client-side filtering --------------------------------------------------

describe('filterMeetings', () => {
  const meetings = [
    makeMeeting('1', { status: 'completed', meeting_type_name: 'daily_standup' }),
    makeMeeting('2', { status: 'in_progress', meeting_type_name: 'sprint_planning' }),
    makeMeeting('3', { status: 'completed', meeting_type_name: 'sprint_planning' }),
    makeMeeting('4', { status: 'failed', meeting_type_name: 'code_review' }),
  ]

  it('returns all when no filters', () => {
    expect(filterMeetings(meetings, {})).toHaveLength(4)
  })

  it('filters by status', () => {
    const result = filterMeetings(meetings, { status: 'completed' })
    expect(result.map((m) => m.meeting_id)).toEqual(['1', '3'])
  })

  it('filters by meeting type', () => {
    const result = filterMeetings(meetings, { meetingType: 'sprint_planning' })
    expect(result.map((m) => m.meeting_id)).toEqual(['2', '3'])
  })

  it('combines multiple filters with AND', () => {
    const result = filterMeetings(meetings, { status: 'completed', meetingType: 'sprint_planning' })
    expect(result.map((m) => m.meeting_id)).toEqual(['3'])
  })
})

// -- Metric helpers ---------------------------------------------------------

describe('countByStatus', () => {
  it('counts meetings with the given status', () => {
    const meetings = [
      makeMeeting('1', { status: 'completed' }),
      makeMeeting('2', { status: 'in_progress' }),
      makeMeeting('3', { status: 'completed' }),
    ]
    expect(countByStatus(meetings, 'completed')).toBe(2)
    expect(countByStatus(meetings, 'in_progress')).toBe(1)
    expect(countByStatus(meetings, 'failed')).toBe(0)
  })
})

describe('totalTokensUsed', () => {
  it('sums total_tokens across meetings with minutes', () => {
    const meetings = [
      makeMeeting('1'), // 650 tokens
      makeMeeting('2'), // 650 tokens
    ]
    expect(totalTokensUsed(meetings)).toBe(1300)
  })

  it('skips meetings without minutes', () => {
    const meetings = [
      makeMeeting('1'), // 650 tokens
      makeMeeting('2', { minutes: null }),
    ]
    expect(totalTokensUsed(meetings)).toBe(650)
  })

  it('returns 0 for empty array', () => {
    expect(totalTokensUsed([])).toBe(0)
  })
})
