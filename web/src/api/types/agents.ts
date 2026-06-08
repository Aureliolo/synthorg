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
  LIFECYCLE_EVENT_TYPE_VALUES as CAREER_EVENT_TYPE_VALUES,
} from './enum-values.gen'

/**
 * AgentConfig with optional dashboard / WS extras layered on top of
 * the wire ``AgentConfig``. ``id`` is the stable agent UUID and now
 * arrives on the wire from every list / get endpoint (derived
 * deterministically from the agent name), so the dashboard addresses
 * agents by it uniformly. ``status`` lives on ``AgentIdentity`` in the
 * persistence layer and arrives via WS agent-updated payloads;
 * ``hiring_date`` is surfaced by the dashboard's projection but is not
 * part of the wire ``AgentConfig``.
 *
 * This type only ADDS optional dashboard extras; it does not tighten or
 * omit any wire field (it is not an ``Omit<Wire, ...> & { ... }`` overlay).
 */
export type AgentConfig = WireAgentConfig & {
  status?: AgentStatus
  hiring_date?: string
}

/**
 * Semantic alias for call sites that want to name the dashboard-context
 * usage of ``AgentConfig`` explicitly.
 */
export type DashboardAgentConfig = AgentConfig
