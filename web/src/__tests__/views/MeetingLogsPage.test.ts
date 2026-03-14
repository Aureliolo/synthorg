import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMeetingStore } from '@/stores/meetings'

vi.mock('@/api/endpoints/meetings', () => ({
  listMeetings: vi.fn().mockResolvedValue({ data: [], total: 0, offset: 0, limit: 50 }),
  getMeeting: vi.fn(),
  triggerMeeting: vi.fn(),
}))

describe('MeetingLogsPage store integration', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('meeting store initializes with empty state', () => {
    const store = useMeetingStore()
    expect(store.meetings).toEqual([])
    expect(store.selectedMeeting).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.total).toBe(0)
  })

  it('meeting store has expected API methods', () => {
    const store = useMeetingStore()
    expect(typeof store.fetchMeetings).toBe('function')
    expect(typeof store.fetchMeeting).toBe('function')
    expect(typeof store.triggerMeeting).toBe('function')
    expect(typeof store.handleWsEvent).toBe('function')
  })
})
