/** Meeting protocol, agenda, contribution and minutes types. */

export type {
  ActionItem,
  MeetingAgenda,
  MeetingAgendaItem,
  MeetingContribution,
  MeetingMinutes,
  MeetingResponse,
  TriggerMeetingRequest,
} from './dtos.gen'

export type {
  MeetingPhase,
  MeetingProtocolType,
  MeetingStatus,
} from './enum-values.gen'
export {
  MEETING_PHASE_VALUES,
  MEETING_PROTOCOL_TYPE_VALUES,
  MEETING_STATUS_VALUES,
} from './enum-values.gen'

/** Frontend-only query filter (not a Pydantic DTO). */
export interface MeetingFilters {
  status?: import('./enum-values.gen').MeetingStatus
  meeting_type?: string
  offset?: number
  limit?: number
}
