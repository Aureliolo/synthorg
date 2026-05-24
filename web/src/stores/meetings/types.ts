import type { StoreApi } from 'zustand'
import type {
  MeetingFilters,
  MeetingResponse,
  TriggerMeetingRequest,
} from '@/api/types/meetings'
import type { WsEvent } from '@/api/types/websocket'

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

export type MeetingsSet = StoreApi<MeetingsState>['setState']
export type MeetingsGet = StoreApi<MeetingsState>['getState']
