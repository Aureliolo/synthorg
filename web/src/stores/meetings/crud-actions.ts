import * as meetingsApi from '@/api/endpoints/meetings'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type {
  MeetingFilters,
  MeetingResponse,
  TriggerMeetingRequest,
} from '@/api/types/meetings'
import {
  getDetailRequestSeq,
  getListRequestSeq,
  getRequestEpoch,
  nextDetailRequestSeq,
  nextListRequestSeq,
} from './_state'
import type { MeetingsGet, MeetingsSet } from './types'

const log = createLogger('meetings')

async function fetchMeetingsImpl(
  set: MeetingsSet,
  get: MeetingsGet,
  filters?: MeetingFilters,
): Promise<void> {
  const epoch = getRequestEpoch()
  const seq = nextListRequestSeq()
  set({ loading: true, error: null })
  try {
    const result = await meetingsApi.listMeetings(filters)
    if (epoch !== getRequestEpoch() || seq !== getListRequestSeq()) return
    // Sync selectedMeeting with fresh data.
    const currentSelected = get().selectedMeeting
    const freshSelected = currentSelected
      ? result.data.find(
          (m) => m.meeting_id === currentSelected.meeting_id,
        ) ?? currentSelected
      : null
    set({
      meetings: result.data,
      total: result.data.length,
      loading: false,
      selectedMeeting: freshSelected,
    })
  } catch (err) {
    if (epoch !== getRequestEpoch() || seq !== getListRequestSeq()) {
      log.warn(
        'Discarding error from stale list request:',
        getErrorMessage(err),
      )
      return
    }
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchMeetingImpl(
  set: MeetingsSet,
  get: MeetingsGet,
  meetingId: string,
): Promise<void> {
  const epoch = getRequestEpoch()
  const seq = nextDetailRequestSeq()
  const current = get().selectedMeeting
  set({
    loadingDetail: true,
    detailError: null,
    selectedMeeting: current?.meeting_id === meetingId ? current : null,
  })
  try {
    const meeting = await meetingsApi.getMeeting(meetingId)
    if (epoch !== getRequestEpoch() || seq !== getDetailRequestSeq()) return
    set({
      selectedMeeting: meeting,
      loadingDetail: false,
      detailError: null,
    })
  } catch (err) {
    if (epoch !== getRequestEpoch() || seq !== getDetailRequestSeq()) {
      log.warn(
        'Discarding error from stale detail request:',
        getErrorMessage(err),
      )
      return
    }
    set({ loadingDetail: false, detailError: getErrorMessage(err) })
  }
}

async function triggerMeetingImpl(
  set: MeetingsSet,
  data: TriggerMeetingRequest,
): Promise<MeetingResponse[]> {
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
}

export function createCrudActions(set: MeetingsSet, get: MeetingsGet) {
  return {
    fetchMeetings: (filters?: MeetingFilters) =>
      fetchMeetingsImpl(set, get, filters),
    fetchMeeting: (meetingId: string) => fetchMeetingImpl(set, get, meetingId),
    triggerMeeting: (data: TriggerMeetingRequest) =>
      triggerMeetingImpl(set, data),
  }
}
