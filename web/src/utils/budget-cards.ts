/**
 * The Budget page header's four metric cards -- pure computations.
 *
 * Split from `budget.ts`, which owns the aggregations these read: the card
 * builders are presentation shaping (labels, sub-text, which trend to draw)
 * and change for different reasons than the arithmetic behind them.
 */

import type { MetricCardProps } from '@/components/ui/metric-card'
import type { ForecastResponse, OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import { computeSpendTrend } from '@/utils/dashboard'
import { formatCurrency } from '@/utils/format'
import {
  computeExhaustionDate,
  daysUntilBudgetReset,
  formatBudgetPercent,
} from '@/utils/budget'

/** One metric card, in the shape `MetricCard` renders. */
export type BudgetMetricCardData = Readonly<Omit<MetricCardProps, 'className'>>

interface BudgetCardContext {
  readonly overview: OverviewMetrics
  readonly budgetConfig: BudgetConfig | null
  readonly forecast: ForecastResponse | null
  readonly currency: string | undefined
}

function _buildSpendCard(ctx: BudgetCardContext): BudgetMetricCardData {
  const { overview, currency } = ctx
  const totalMonthly = ctx.budgetConfig?.total_monthly ?? 0
  const hasBudget = totalMonthly > 0
  return {
    label: 'SPEND THIS PERIOD',
    value: formatCurrency(overview.total_cost, currency),
    sparklineData: overview.cost_7d_trend.map((p) => p.value),
    change: computeSpendTrend(overview.cost_7d_trend),
    ...(hasBudget && {
      progress: { current: overview.total_cost, total: totalMonthly },
      subText: `of ${formatCurrency(totalMonthly, currency)} budget`,
    }),
  }
}

function _buildRemainingCard(ctx: BudgetCardContext): BudgetMetricCardData {
  const { overview, currency } = ctx
  return {
    label: 'BUDGET REMAINING',
    value: formatCurrency(overview.budget_remaining, currency),
    subText: formatBudgetPercent(
      Math.max(0, 100 - overview.budget_used_percent),
      overview.budget_measurability,
      ' of budget',
    ),
  }
}

function _buildAvgDayCard(ctx: BudgetCardContext): BudgetMetricCardData {
  return {
    label: 'AVG DAILY SPEND',
    value: formatCurrency(ctx.forecast?.avg_daily_spend ?? 0, ctx.currency),
  }
}

/**
 * Pick the sub-text line for the "days until exhausted" card. When the
 * forecast knows the exhaustion horizon we render its calendar date;
 * otherwise we fall back to the next budget-reset countdown.
 */
function _daysLeftSubText(ctx: BudgetCardContext): string | undefined {
  const days = ctx.forecast?.days_until_exhausted
  if (days != null) {
    return computeExhaustionDate(days) ?? undefined
  }
  if (ctx.budgetConfig === null) return undefined
  return `Resets in ${daysUntilBudgetReset(ctx.budgetConfig.reset_day)} days`
}

function _buildDaysLeftCard(ctx: BudgetCardContext): BudgetMetricCardData {
  const days = ctx.forecast?.days_until_exhausted
  return {
    label: 'DAYS UNTIL EXHAUSTED',
    value: days != null ? String(days) : 'N/A',
    subText: _daysLeftSubText(ctx),
  }
}

/**
 * Compute metric card data for the Budget page header.
 *
 * Returns an array of 4 card definitions matching the MetricCard props shape.
 */
export function computeBudgetMetricCards(
  overview: OverviewMetrics,
  budgetConfig: BudgetConfig | null,
  forecast: ForecastResponse | null,
): BudgetMetricCardData[] {
  const ctx: BudgetCardContext = {
    overview,
    budgetConfig,
    forecast,
    currency: overview.currency,
  }
  return [
    _buildSpendCard(ctx),
    _buildRemainingCard(ctx),
    _buildAvgDayCard(ctx),
    _buildDaysLeftCard(ctx),
  ]
}
