import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMeetingStore } from '@/stores/meetings'
import type { MeetingRecord, WsEvent } from '@/api/types'
import { flushPromises } from '@vue/test-utils'

const mockGetMeeting = vi.fn()
const mockListMeetings = vi.fn()
const mockTriggerMeeting = vi.fn()

vi.mock('@/api/endpoints/meetings', () => ({
  listMeetings: (...args: unknown[]) => mockListMeetings(...args),
  getMeeting: (...args: unknown[]) => mockGetMeeting(...args),
  triggerMeeting: (...args: unknown[]) => mockTriggerMeeting(...args),
}))

const completedRecord: MeetingRecord = {
  meeting_id: 'mtg-1',
  meeting_type_name: 'standup',
  protocol_type: 'round_robin',
  status: 'completed',
  minutes: null,
  error_message: null,
  token_budget: 2000,
}

describe('useMeetingStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGetMeeting.mockReset()
    mockListMeetings.mockReset()
    mockTriggerMeeting.mockReset()
  })

  it('initializes with empty state', () => {
    const store = useMeetingStore()
    expect(store.meetings).toEqual([])
    expect(store.selectedMeeting).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.total).toBe(0)
  })

  it('handles meeting.completed WS event by re-fetching full record', async () => {
    const store = useMeetingStore()
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

    mockGetMeeting.mockResolvedValue(completedRecord)

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
    await flushPromises()

    expect(mockGetMeeting).toHaveBeenCalledWith('mtg-1')
    expect(store.meetings[0].status).toBe('completed')
  })

  it('appends new meeting from WS event when not in local list', async () => {
    const store = useMeetingStore()
    mockGetMeeting.mockResolvedValue(completedRecord)

    const event: WsEvent = {
      event_type: 'meeting.started',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: { meeting_id: 'mtg-1' },
    }
    store.handleWsEvent(event)
    await flushPromises()

    expect(mockGetMeeting).toHaveBeenCalledWith('mtg-1')
    expect(store.meetings).toHaveLength(1)
    expect(store.total).toBe(1)
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

  it('ignores WS events from other channels', () => {
    const store = useMeetingStore()
    const event: WsEvent = {
      event_type: 'meeting.completed',
      channel: 'tasks',
      timestamp: '2026-03-14T10:00:00Z',
      payload: { meeting_id: 'mtg-1' },
    }
    store.handleWsEvent(event)
    expect(mockGetMeeting).not.toHaveBeenCalled()
  })

  it('syncs selectedMeeting on WS refresh', async () => {
    const store = useMeetingStore()
    store.meetings = [{ ...completedRecord, status: 'in_progress' }]
    store.selectedMeeting = { ...completedRecord, status: 'in_progress' }

    mockGetMeeting.mockResolvedValue(completedRecord)

    const event: WsEvent = {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: { meeting_id: 'mtg-1' },
    }
    store.handleWsEvent(event)
    await flushPromises()

    expect(store.selectedMeeting?.status).toBe('completed')
  })

  it('fetchMeetings updates state from API response', async () => {
    const store = useMeetingStore()
    mockListMeetings.mockResolvedValue({
      data: [completedRecord],
      total: 1,
      offset: 0,
      limit: 50,
    })

    await store.fetchMeetings()

    expect(store.meetings).toEqual([completedRecord])
    expect(store.total).toBe(1)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('fetchMeetings sets error on failure', async () => {
    const store = useMeetingStore()
    mockListMeetings.mockRejectedValue(new Error('Network error'))

    await store.fetchMeetings()

    expect(store.error).toBe('Network error')
    expect(store.loading).toBe(false)
  })

  it('triggerMeeting appends new records with dedup', async () => {
    const store = useMeetingStore()
    store.meetings = [completedRecord]

    const newRecord: MeetingRecord = {
      ...completedRecord,
      meeting_id: 'mtg-2',
    }
    mockTriggerMeeting.mockResolvedValue([completedRecord, newRecord])

    const result = await store.triggerMeeting({ event_name: 'test' })

    expect(result).toHaveLength(2)
    // mtg-1 already existed, only mtg-2 should be appended
    expect(store.meetings).toHaveLength(2)
    expect(store.meetings[1].meeting_id).toBe('mtg-2')
  })

  it('triggerMeeting returns null on error', async () => {
    const store = useMeetingStore()
    mockTriggerMeeting.mockRejectedValue(new Error('Server error'))

    const result = await store.triggerMeeting({ event_name: 'test' })

    expect(result).toBeNull()
    expect(store.error).toBe('Server error')
  })

  it('handles WS re-fetch failure gracefully', async () => {
    const store = useMeetingStore()
    mockGetMeeting.mockRejectedValue(new Error('Network error'))

    const event: WsEvent = {
      event_type: 'meeting.failed',
      channel: 'meetings',
      timestamp: '2026-03-14T10:00:00Z',
      payload: { meeting_id: 'mtg-fail' },
    }
    store.handleWsEvent(event)
    await flushPromises()

    // Should not crash, meetings remain unchanged
    expect(store.meetings).toHaveLength(0)
  })
})
