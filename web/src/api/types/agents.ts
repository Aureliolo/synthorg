/** Agent config, performance, activity and career event types. */

import type {
  AgentStatus,
  AutonomyLevel,
  DepartmentName,
  SeniorityLevel,
} from './enums'

export type AgentTier = 'large' | 'medium' | 'small'

export type StrategicOutputMode =
  | 'mission_first'
  | 'evidence_first'
  | 'option_first'

/**
 * Agent configuration as returned by the /agents API endpoints.
 *
 * Matches the backend AgentConfig Pydantic model (config/schema.py).
 * Runtime fields (id, status, hiring_date) are optional; they exist
 * on AgentIdentity but may not be present in config-level responses.
 * Wizard round-trip fields (personality_preset, strategic_output_mode,
 * tier, model_requirement) are optional and null when the agent was
 * not built through the setup wizard.
 */
export interface AgentConfig {
  id?: string
  name: string
  role: string
  department: DepartmentName
  level: SeniorityLevel
  status?: AgentStatus
  personality: Record<string, unknown>
  personality_preset?: string | null
  strategic_output_mode?: StrategicOutputMode | null
  model: Record<string, unknown>
  memory: Record<string, unknown>
  tools: Record<string, unknown>
  authority: Record<string, unknown>
  autonomy_level: AutonomyLevel | null
  hiring_date?: string
  tier?: AgentTier | null
  model_requirement?: Record<string, unknown> | null
}

export type TrendDirection = 'improving' | 'stable' | 'declining' | 'insufficient_data'

export const TREND_DIRECTION_VALUES = [
  'improving', 'stable', 'declining', 'insufficient_data',
] as const satisfies readonly TrendDirection[]

/**
 * Aggregate metrics for a rolling time window.
 * Invariant: tasks_completed + tasks_failed === data_point_count (enforced server-side).
 */
export interface WindowMetrics {
  window_size: string
  data_point_count: number
  tasks_completed: number
  tasks_failed: number
  avg_quality_score: number | null
  avg_cost_per_task: number | null
  avg_completion_time_seconds: number | null
  avg_tokens_per_task: number | null
  success_rate: number | null
  collaboration_score: number | null
}

export interface TrendResult {
  metric_name: string
  window_size: string
  direction: TrendDirection
  slope: number
  data_point_count: number
}

export interface AgentPerformanceSummary {
  agent_name: string
  tasks_completed_total: number
  tasks_completed_7d: number
  tasks_completed_30d: number
  avg_completion_time_seconds: number | null
  success_rate_percent: number | null
  cost_per_task: number | null
  quality_score: number | null
  collaboration_score: number | null
  trend_direction: TrendDirection
  readonly windows: readonly WindowMetrics[]
  readonly trends: readonly TrendResult[]
}

export type ActivityEventType =
  | 'hired' | 'fired' | 'promoted' | 'demoted' | 'onboarded'
  | 'offboarded' | 'status_changed'
  | 'task_completed' | 'task_started'
  | 'cost_incurred'
  | 'tool_used'
  | 'delegation_sent' | 'delegation_received'

export const ACTIVITY_EVENT_TYPE_VALUES = [
  'hired', 'fired', 'promoted', 'demoted', 'onboarded',
  'offboarded', 'status_changed',
  'task_completed', 'task_started',
  'cost_incurred',
  'tool_used',
  'delegation_sent', 'delegation_received',
] as const satisfies readonly ActivityEventType[]

export interface AgentActivityEvent {
  event_type: ActivityEventType | (string & {})
  timestamp: string
  description: string
  readonly related_ids: Readonly<Record<string, string>>
}

export type CareerEventType = 'hired' | 'fired' | 'promoted' | 'demoted' | 'onboarded'

export const CAREER_EVENT_TYPE_VALUES = [
  'hired', 'fired', 'promoted', 'demoted', 'onboarded',
] as const satisfies readonly CareerEventType[]

export interface CareerEvent {
  event_type: CareerEventType
  timestamp: string
  description: string
  initiated_by: string
  readonly metadata: Readonly<Record<string, string>>
}
