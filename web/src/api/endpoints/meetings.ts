import { apiClient, unwrap, unwrapPaginated } from '../client'
import type {
  ApiResponse,
  MeetingFilters,
  MeetingRecord,
  PaginatedResponse,
  TriggerMeetingRequest,
} from '../types'

export async function listMeetings(filters?: MeetingFilters) {
  const response = await apiClient.get<PaginatedResponse<MeetingRecord>>('/meetings', {
    params: filters,
  })
  return unwrapPaginated<MeetingRecord>(response)
}

export async function getMeeting(meetingId: string): Promise<MeetingRecord> {
  const response = await apiClient.get<ApiResponse<MeetingRecord>>(
    `/meetings/${encodeURIComponent(meetingId)}`,
  )
  return unwrap(response)
}

export async function triggerMeeting(
  data: TriggerMeetingRequest,
): Promise<MeetingRecord[]> {
  const response = await apiClient.post<ApiResponse<MeetingRecord[]>>('/meetings/trigger', data)
  return unwrap(response)
}
