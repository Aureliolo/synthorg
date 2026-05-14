/** Agent config, performance, activity and career event types. */

import type { AgentConfig as WireAgentConfig } from './dtos.gen'
import type { StrategicOutputMode } from './enum-values.gen'
import type {
  AgentStatus,
  AutonomyLevel,
  DepartmentName,
  SeniorityLevel,
} from './enums'

export type {
  ActivityEvent as AgentActivityEvent,
  AgentPerformanceSummary,
  CareerEvent,
  RecommendedAction,
  TrendResult,
  WindowMetrics,
} from './dtos.gen'

export type {
  ActivityEventType,
  LifecycleEventType as CareerEventType,
  StrategicOutputMode,
  TrendDirection,
} from './enum-values.gen'

export {
  ACTIVITY_EVENT_TYPE_VALUES,
  LIFECYCLE_EVENT_TYPE_VALUES as CAREER_EVENT_TYPE_VALUES,
  STRATEGIC_OUTPUT_MODE_VALUES,
  TREND_DIRECTION_VALUES,
} from './enum-values.gen'

/** Frontend-only inline-union mirror of the wire's
 *  ``tier?: "large" | "medium" | "small"`` field type used on the
 *  setup wizard outputs and the agent-config form. */
export type AgentTier = 'large' | 'medium' | 'small'

/**
 * AgentConfig with the runtime / display fields the dashboard relies
 * on overlaid on top of the wire's ``WireAgentConfig`` (the latter
 * only carries the config-time fields; ``id`` and ``status`` live on
 * ``AgentIdentity`` in the persistence layer and arrive via the WS
 * agent-updated payloads, not the HTTP list / get endpoints). Strict
 * frontend enums (``DepartmentName``, ``SeniorityLevel``) are
 * preserved because the dashboard's stores narrow the values
 * defensively at the boundary.
 */
export type AgentConfig = Omit<
  WireAgentConfig,
  'department' | 'level' | 'personality' | 'model' | 'memory' | 'tools' | 'authority' | 'autonomy_level'
> & {
  id?: string
  status?: AgentStatus
  department: DepartmentName
  level: SeniorityLevel
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
