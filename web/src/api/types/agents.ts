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
 * The wire's required-vs-optional shape is now correct out of the
 * generator, so this type only ADDS optional extras: it is NOT an
 * ``Omit<Wire, ...> & { ... }`` tightening overlay.
 */
export type AgentConfig = WireAgentConfig & {
  status?: AgentStatus
  hiring_date?: string
}

/**
 * Alias retained for call sites that want to mark the dashboard view
 * explicitly. New code should prefer ``AgentConfig`` directly.
 */
export type DashboardAgentConfig = AgentConfig
