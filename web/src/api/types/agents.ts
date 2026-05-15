/** Agent config, performance, activity and career event types. */

import type { AgentConfig as WireAgentConfig } from './dtos.gen'
import type { AgentStatus } from './enums'

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

/** Frontend alias derived from the wire type so the union stays in
 *  lockstep with the generated ``AgentConfig.tier`` field. */
export type AgentTier = NonNullable<WireAgentConfig['tier']>

/**
 * AgentConfig with optional dashboard / WS extras layered on top of
 * the wire ``AgentConfig``. ``id`` and ``status`` live on
 * ``AgentIdentity`` in the persistence layer and arrive on the
 * dashboard via WS agent-updated payloads (the HTTP list / get
 * endpoints return the config-time shape only). ``hiring_date`` is
 * surfaced by the dashboard's projection but is not part of the
 * wire ``AgentConfig``.
 *
 * The wire's required-vs-optional shape is now correct out of the
 * generator, so this type only ADDS optional extras: it is NOT an
 * ``Omit<Wire, ...> & { ... }`` tightening overlay.
 */
export type AgentConfig = WireAgentConfig & {
  id?: string
  status?: AgentStatus
  hiring_date?: string
}

/**
 * Alias retained for call sites that want to mark the dashboard view
 * explicitly. New code should prefer ``AgentConfig`` directly.
 */
export type DashboardAgentConfig = AgentConfig
