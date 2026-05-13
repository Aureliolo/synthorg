/**
 * Meeting mock-data builders.
 */

export interface MockMeeting {
  id: string
  name: string
  type: 'daily_standup' | 'sprint_planning' | 'design_review' | 'retrospective'
  status: 'scheduled' | 'in_progress' | 'completed' | 'failed'
  started_at: string | null
  ended_at: string | null
  participants: readonly string[]
}

export function makeMeeting(overrides: Partial<MockMeeting> = {}): MockMeeting {
  return {
    id: 'meeting-001',
    name: 'Daily Standup',
    type: 'daily_standup',
    status: 'scheduled',
    started_at: null,
    ended_at: null,
    participants: ['agent-001', 'agent-002'],
    ...overrides,
  }
}
