import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMeetingStore } from '@/stores/meetings'
import type { WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/meetings', () => ({
  listMeetings: vi.fn(),
  getMeeting: vi.fn(),
  triggerMeeting: vi.fn(),
}))

describe('useMeetingStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initializes with empty state', () => {
    const store = useMeetingStore()
    expect(store.meetings).toEqual([])
    expect(store.selectedMeeting).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.total).toBe(0)
  })

  it('handles meeting.completed WS event for existing meeting', () => {
    const store = useMeetingStore()
    // Pre-populate a meeting
    store.meetings = [
      {
        meeting_id: 'mtg-1',
        meeting_type_name: 'standup',
        protocol_type: 'round_robin',
        status: 'in_progress',
        minutes: null,
        error_message: null,
        token_budget: 2000,
      },
    ]

    const event: WsEvent = {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: {
        meeting_id: 'mtg-1',
        status: 'completed',
      },
    }
    store.handleWsEvent(event)
    expect(store.meetings[0].status).toBe('completed')
  })

  it('ignores WS event for unknown meeting', () => {
    const store = useMeetingStore()
    const event: WsEvent = {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: {
        meeting_id: 'mtg-unknown',
        status: 'completed',
      },
    }
    store.handleWsEvent(event)
    expect(store.meetings).toHaveLength(0)
  })

  it('ignores WS event with malformed payload', () => {
    const store = useMeetingStore()
    const event: WsEvent = {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: {},
    }
    store.handleWsEvent(event)
    expect(store.meetings).toHaveLength(0)
  })
})
