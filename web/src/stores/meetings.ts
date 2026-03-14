import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as meetingsApi from '@/api/endpoints/meetings'
import { getErrorMessage } from '@/utils/errors'
import type {
  MeetingRecord,
  MeetingFilters,
  TriggerMeetingRequest,
  WsEvent,
} from '@/api/types'

export const useMeetingStore = defineStore('meetings', () => {
  const meetings = ref<MeetingRecord[]>([])
  const selectedMeeting = ref<MeetingRecord | null>(null)
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchMeetings(filters?: MeetingFilters) {
    loading.value = true
    error.value = null
    try {
      const result = await meetingsApi.listMeetings(filters)
      meetings.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchMeeting(meetingId: string) {
    loading.value = true
    error.value = null
    try {
      selectedMeeting.value = await meetingsApi.getMeeting(meetingId)
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function triggerMeeting(data: TriggerMeetingRequest): Promise<MeetingRecord[] | null> {
    loading.value = true
    error.value = null
    try {
      const records = await meetingsApi.triggerMeeting(data)
      // Append new records not already in local list (batched)
      const existingIds = new Set(meetings.value.map((m) => m.meeting_id))
      const newRecords = records.filter((r) => !existingIds.has(r.meeting_id))
      if (newRecords.length > 0) {
        meetings.value = [...meetings.value, ...newRecords]
        total.value += newRecords.length
      }
      return records
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    } finally {
      loading.value = false
    }
  }

  /** Re-fetch a single meeting and update or append it in the local list. */
  async function _refreshMeeting(meetingId: string) {
    try {
      const fresh = await meetingsApi.getMeeting(meetingId)
      const idx = meetings.value.findIndex((m) => m.meeting_id === meetingId)
      if (idx >= 0) {
        meetings.value = meetings.value.map((m) =>
          m.meeting_id === meetingId ? fresh : m,
        )
      } else {
        meetings.value = [...meetings.value, fresh]
        total.value++
      }
      if (selectedMeeting.value?.meeting_id === meetingId) {
        selectedMeeting.value = fresh
      }
    } catch (err) {
      console.warn('Meeting refresh failed:', meetingId, err)
    }
  }

  function handleWsEvent(event: WsEvent) {
    if (event.channel !== 'meetings') return
    const payload = event.payload as Record<string, unknown> | null
    if (!payload || typeof payload !== 'object') return

    switch (event.event_type) {
      case 'meeting.completed':
      case 'meeting.started':
      case 'meeting.failed': {
        const meetingId = payload.meeting_id
        if (typeof meetingId === 'string' && meetingId) {
          // Re-fetch the full record to get minutes/error details
          void _refreshMeeting(meetingId)
        }
        break
      }
    }
  }

  return {
    meetings,
    selectedMeeting,
    total,
    loading,
    error,
    fetchMeetings,
    fetchMeeting,
    triggerMeeting,
    handleWsEvent,
  }
})
