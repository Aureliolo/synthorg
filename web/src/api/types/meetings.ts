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

import type { MeetingResponse } from './dtos.gen'

/** MeetingRecord is the base shape that ``MeetingResponse`` extends
 *  with the computed analytics fields. The wire never returns
 *  ``MeetingRecord`` alone (the controller always projects to
 *  ``MeetingResponse``), so deriving the base from the response keeps
 *  a single source of truth and avoids re-declaring the column set. */
export type MeetingRecord = Omit<
  MeetingResponse,
  'token_usage_by_participant' | 'contribution_rank' | 'meeting_duration_seconds'
>

/** Frontend-only query filter (not a Pydantic DTO). */
export interface MeetingFilters {
  status?: import('./enum-values.gen').MeetingStatus
  meeting_type?: string
  offset?: number
  limit?: number
}
