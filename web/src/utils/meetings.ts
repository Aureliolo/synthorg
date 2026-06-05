import type { MeetingPhase, MeetingProtocolType, MeetingResponse, MeetingStatus } from '@/api/types/meetings'
import type { SemanticColor } from '@/lib/utils'
import { formatUptime } from '@/utils/format'

// -- Meeting status color mapping -------------------------------------------

const MEETING_STATUS_COLOR_MAP: Record<MeetingStatus, SemanticColor | 'text-secondary'> = {
  scheduled: 'accent',
  in_progress: 'warning',
  completed: 'success',
  failed: 'danger',
  cancelled: 'text-secondary',
  budget_exhausted: 'danger',
}

export function getMeetingStatusColor(status: MeetingStatus): SemanticColor | 'text-secondary' {
  return MEETING_STATUS_COLOR_MAP[status]
}

// -- Meeting status labels --------------------------------------------------

const MEETING_STATUS_LABELS: Record<MeetingStatus, string> = {
  scheduled: 'Scheduled',
  in_progress: 'In Progress',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  budget_exhausted: 'Budget Exhausted',
}

export function getMeetingStatusLabel(status: MeetingStatus): string {
  return MEETING_STATUS_LABELS[status]
}

// -- Protocol labels --------------------------------------------------------

const PROTOCOL_LABELS: Record<MeetingProtocolType, string> = {
  round_robin: 'Round Robin',
  position_papers: 'Position Papers',
  structured_phases: 'Structured Phases',
}

export function getProtocolLabel(protocol: MeetingProtocolType): string {
  return PROTOCOL_LABELS[protocol]
}

// -- Phase labels -----------------------------------------------------------

const PHASE_LABELS: Record<MeetingPhase, string> = {
  agenda_broadcast: 'Agenda Broadcast',
  round_robin_turn: 'Round Robin Turn',
  position_paper: 'Position Paper',
  input_gathering: 'Input Gathering',
  discussion: 'Discussion',
  synthesis: 'Synthesis',
  summary: 'Summary',
  premortem: 'Premortem',
  devil_advocate: "Devil's Advocate",
}

export function getPhaseLabel(phase: MeetingPhase): string {
  return PHASE_LABELS[phase]
}

// -- Phase colors -----------------------------------------------------------

const PHASE_COLOR_MAP: Record<MeetingPhase, SemanticColor> = {
  agenda_broadcast: 'accent',
  round_robin_turn: 'accent',
  position_paper: 'accent',
  input_gathering: 'accent',
  discussion: 'warning',
  synthesis: 'success',
  summary: 'success',
  premortem: 'warning',
  devil_advocate: 'warning',
}

export function getPhaseColor(phase: MeetingPhase): SemanticColor {
  return PHASE_COLOR_MAP[phase]
}

// -- Token helpers ----------------------------------------------------------

export function computeTokenUsagePercent(meeting: MeetingResponse): number {
  if (!meeting.minutes || meeting.token_budget <= 0) return 0
  const pct = (meeting.minutes.total_tokens / meeting.token_budget) * 100
  return Math.min(100, Math.max(0, pct))
}

export function getParticipantTokenShare(meeting: MeetingResponse, agentId: string): number {
  if (!meeting.minutes || meeting.minutes.total_tokens <= 0) return 0
  const usage = meeting.token_usage_by_participant[agentId]
  if (usage === undefined || usage <= 0) return 0
  const pct = (usage / meeting.minutes.total_tokens) * 100
  return Math.min(100, Math.max(0, pct))
}

// -- Duration formatting ----------------------------------------------------

export function formatMeetingDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return '--'
  return formatUptime(seconds)
}

// -- Status dot CSS class ---------------------------------------------------

export const STATUS_DOT_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  danger: 'bg-danger',
  warning: 'bg-warning',
  accent: 'bg-accent',
  success: 'bg-success',
  'text-secondary': 'bg-muted-foreground',
}

// -- Status badge CSS classes -----------------------------------------------

export const STATUS_BADGE_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  danger: 'border-danger/30 bg-danger/10 text-danger',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  accent: 'border-accent/30 bg-accent/10 text-accent',
  success: 'border-success/30 bg-success/10 text-success',
  'text-secondary': 'border-border bg-surface text-text-secondary',
}

// -- Client-side filtering --------------------------------------------------

export interface MeetingPageFilters {
  status?: MeetingStatus
  meetingType?: string
}

export function filterMeetings(
  meetings: readonly MeetingResponse[],
  filters: MeetingPageFilters,
): MeetingResponse[] {
  let result = [...meetings]

  if (filters.status) {
    result = result.filter((m) => m.status === filters.status)
  }

  if (filters.meetingType) {
    result = result.filter((m) => m.meeting_type_name === filters.meetingType)
  }

  return result
}

// -- Metric helpers ---------------------------------------------------------

export function countByStatus(meetings: readonly MeetingResponse[], status: MeetingStatus): number {
  return meetings.filter((m) => m.status === status).length
}

export function totalTokensUsed(meetings: readonly MeetingResponse[]): number {
  let total = 0
  for (const m of meetings) {
    if (m.minutes) total += m.minutes.total_tokens
  }
  return total
}
