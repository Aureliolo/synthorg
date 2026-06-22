import { useEffect, useMemo, useState } from 'react'
import { getParetoFrontier } from '@/api/endpoints/budget'
import type { ParetoFrontier } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { useBudgetData } from '@/hooks/useBudgetData'
import {
  computeAgentSpending,
  computeBudgetMetricCards,
  computeCategoryBreakdown,
  computeCostBreakdown,
  filterCfoEvents,
  getThresholdZone,
  type BreakdownDimension,
} from '@/utils/budget'
import { useBudgetForecastStore } from '@/stores/budgetForecast'

const log = createLogger('budget-page')

export type BudgetData = ReturnType<typeof useBudgetData>
export type CurrentForecast = ReturnType<typeof useBudgetForecastStore.getState>['current']

export interface ParetoFrontierState {
  paretoFrontier: ParetoFrontier | null
  paretoLoading: boolean
  /**
   * Set when the frontier request failed, so the page can distinguish
   * "not configured" (null + no error) from "fetch failed" and render a
   * failure banner instead of a silent empty section.
   */
  paretoError: string | null
}

export function useParetoFrontier(): ParetoFrontierState {
  const [paretoFrontier, setParetoFrontier] = useState<ParetoFrontier | null>(null)
  const [paretoLoading, setParetoLoading] = useState<boolean>(true)
  const [paretoError, setParetoError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getParetoFrontier(controller.signal)
      .then((frontier) => {
        if (!controller.signal.aborted) {
          setParetoFrontier(frontier)
          setParetoError(null)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        log.warn('failed to load pareto frontier', err)
        setParetoFrontier(null)
        setParetoError(getErrorMessage(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) setParetoLoading(false)
      })
    return () => {
      controller.abort()
    }
  }, [])

  return { paretoFrontier, paretoLoading, paretoError }
}

export interface BudgetDerived {
  currency: string | undefined
  thresholdZone: ReturnType<typeof getThresholdZone>
  metricCards: ReturnType<typeof computeBudgetMetricCards>
  agentSpendingRows: ReturnType<typeof computeAgentSpending>
  costBreakdown: ReturnType<typeof computeCostBreakdown>
  categoryRatio: ReturnType<typeof computeCategoryBreakdown>
  cfoEvents: ReturnType<typeof filterCfoEvents>
}

export function useBudgetDerived(
  data: BudgetData,
  breakdownDimension: BreakdownDimension,
): BudgetDerived {
  const { overview, budgetConfig, forecast, costRecords, activities, agentNameMap, agentDeptMap } =
    data

  const thresholdZone = useMemo(
    () =>
      overview && budgetConfig
        ? getThresholdZone(overview.budget_used_percent, budgetConfig.alerts)
        : ('normal' as const),
    [overview, budgetConfig],
  )
  const metricCards = useMemo(
    () => (overview ? computeBudgetMetricCards(overview, budgetConfig, forecast) : []),
    [overview, budgetConfig, forecast],
  )
  const agentSpendingRows = useMemo(
    () => computeAgentSpending(costRecords, budgetConfig?.total_monthly ?? 0, agentNameMap),
    [costRecords, budgetConfig, agentNameMap],
  )
  const costBreakdown = useMemo(
    () => computeCostBreakdown(costRecords, breakdownDimension, agentNameMap, agentDeptMap),
    [costRecords, breakdownDimension, agentNameMap, agentDeptMap],
  )
  const categoryRatio = useMemo(() => computeCategoryBreakdown(costRecords), [costRecords])
  const cfoEvents = useMemo(() => filterCfoEvents(activities), [activities])

  return {
    currency: overview?.currency ?? budgetConfig?.currency,
    thresholdZone,
    metricCards,
    agentSpendingRows,
    costBreakdown,
    categoryRatio,
    cfoEvents,
  }
}

export interface ForecastActions {
  forecastMutating: boolean
  approveForecast: ReturnType<typeof useBudgetForecastStore.getState>['approveForecast']
  rejectForecast: ReturnType<typeof useBudgetForecastStore.getState>['rejectForecast']
  raiseCeiling: ReturnType<typeof useBudgetForecastStore.getState>['raiseCeiling']
}

export function useForecastActions(): ForecastActions {
  return {
    forecastMutating: useBudgetForecastStore((s) => s.mutating),
    approveForecast: useBudgetForecastStore((s) => s.approveForecast),
    rejectForecast: useBudgetForecastStore((s) => s.rejectForecast),
    raiseCeiling: useBudgetForecastStore((s) => s.raiseCeiling),
  }
}
