/** System health and per-agent autonomy level types. */

export type {
  AutonomyLevelRequest,
  AutonomyLevelResponse,
  LivenessStatus,
  ReadinessStatus,
  ReadinessStatus as HealthStatus,
} from './dtos.gen'

export type { ReadinessOutcome, TelemetryStatus } from './enum-values.gen'
export { READINESS_OUTCOME_VALUES, TELEMETRY_STATUS_VALUES } from './enum-values.gen'
