/** Control-plane query types: tool audit, agent trust and security export. */

export type {
  AgentHealthResponse,
  AuditEntry,
  CoordinationMetrics,
  CoordinationMetricsRecord,
  MessageOverhead,
  PerformanceSummary,
  SecurityConfigExportResponse,
  TrustSummary,
} from './dtos.gen'

export type { ToolCategory } from './enum-values.gen'
export { TOOL_CATEGORY_VALUES } from './enum-values.gen'

/** Frontend-only audit verdict union (the wire surfaces the value as
 *  a string field on AuditEntry; the inline literal stays here so
 *  the dashboard's verdict pickers remain strict). */
export type AuditVerdictStr = 'allow' | 'deny' | 'escalate' | 'output_scan'
