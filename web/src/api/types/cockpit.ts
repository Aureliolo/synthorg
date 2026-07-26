/** Mission-control cockpit: live activity, flight recorder, interrupts and red-team review types. */

export type {
  AgentActivity,
  FlightRecorderFrame,
  InterruptResponse,
  LiveActivitySnapshot,
  RedTeamFinding,
  RedTeamReportRecord,
  ReplaySeekView,
  ResumeInterruptRequest,
} from './dtos.gen'

export type { RedTeamSeverity, RedTeamVerdict } from './enum-values.gen'
