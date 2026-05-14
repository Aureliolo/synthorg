/** Control-plane query types: tool audit, agent trust and security export. */

import type {
  AgentHealthResponse as WireAgentHealthResponse,
  AuditEntry as WireAuditEntry,
  CoordinationMetrics as WireCoordinationMetrics,
  CoordinationMetricsRecord as WireCoordinationMetricsRecord,
  MessageOverhead,
  PerformanceSummary as WirePerformanceSummary,
  SecurityConfigExportResponse as WireSecurityConfigExportResponse,
  TrustSummary as WireTrustSummary,
} from './dtos.gen'

export type { ToolCategory } from './enum-values.gen'
export { TOOL_CATEGORY_VALUES } from './enum-values.gen'

import type { ApprovalRiskLevel } from './enums'
import type { ToolCategory } from './enum-values.gen'

/** Frontend-only audit verdict union (the wire surfaces the value as
 *  a string field on AuditEntry; the inline literal stays here so
 *  the dashboard's verdict pickers remain strict). */
export type AuditVerdictStr = 'allow' | 'deny' | 'escalate' | 'output_scan'

/** Promote the defaulted fields to required (the wire always emits
 *  them). The intersection re-types nullable score / timestamp /
 *  trend fields to required-but-nullable to match the dashboard's
 *  expectation that the field is present even when its value is null. */
export type TrustSummary = Required<WireTrustSummary>
export type PerformanceSummary = Required<WirePerformanceSummary>

export type AgentHealthResponse = Required<WireAgentHealthResponse>

export type AuditEntry = Omit<
  WireAuditEntry,
  'agent_id' | 'task_id' | 'tool_category' | 'risk_level' | 'matched_rules' | 'reason' | 'evaluation_duration_ms' | 'confidence' | 'approval_id'
> & {
  readonly agent_id: string | null
  readonly task_id: string | null
  readonly tool_category: ToolCategory
  readonly risk_level: ApprovalRiskLevel
  readonly matched_rules: readonly string[]
  readonly reason: string
  readonly evaluation_duration_ms: number
  readonly confidence: 'high' | 'low'
  readonly approval_id: string | null
}

export type MessageOverheadPayload = Required<MessageOverhead>
export type CoordinationMetricsPayload = Required<WireCoordinationMetrics>
export type CoordinationMetricsRecord = Required<WireCoordinationMetricsRecord>

export type SecurityConfigExportResponse = Required<WireSecurityConfigExportResponse>
