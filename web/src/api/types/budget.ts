/** Budget, cost tracking and spending types. */

import type {
  AgentSpending as WireAgentSpending,
  AutoDowngradeConfig as WireAutoDowngradeConfig,
  BudgetAlertConfig as WireBudgetAlertConfig,
  BudgetConfig as WireBudgetConfig,
  CostRecord as WireCostRecord,
  DailySummary as WireDailySummary,
  PeriodSummary as WirePeriodSummary,
} from './dtos.gen'

export type { FinishReason } from './enum-values.gen'
export { FINISH_REASON_VALUES } from './enum-values.gen'

import type { FinishReason } from './enum-values.gen'

/** Promote Pydantic-defaulted fields to required. The wire emits
 *  each of them on every response (defaults are serialised), so
 *  consumer code can rely on the value being present. */
export type CostRecord = Omit<
  WireCostRecord,
  | 'call_category'
  | 'accuracy_effort_ratio'
  | 'latency_ms'
  | 'cache_hit'
  | 'retry_count'
  | 'retry_reason'
  | 'finish_reason'
  | 'success'
  | 'project_id'
> & {
  readonly project_id: string | null
  readonly call_category: 'productive' | 'coordination' | 'system' | 'embedding' | null
  readonly accuracy_effort_ratio: number | null
  readonly latency_ms: number | null
  readonly cache_hit: boolean | null
  readonly retry_count: number | null
  readonly retry_reason: string | null
  readonly finish_reason: FinishReason | null
  readonly success: boolean | null
}

export type DailySummary = Required<WireDailySummary>
export type PeriodSummary = Required<WirePeriodSummary>
export type BudgetAlertConfig = Required<WireBudgetAlertConfig>
export type AutoDowngradeConfig = Required<WireAutoDowngradeConfig>
export type AgentSpending = Required<WireAgentSpending>

export type BudgetConfig = Omit<
  WireBudgetConfig,
  'alerts' | 'auto_downgrade' | 'per_task_limit' | 'per_agent_daily_limit' | 'reset_day' | 'currency'
> & {
  readonly alerts: BudgetAlertConfig
  readonly auto_downgrade: AutoDowngradeConfig
  readonly per_task_limit: number
  readonly per_agent_daily_limit: number
  readonly reset_day: number
  readonly currency: string
}
