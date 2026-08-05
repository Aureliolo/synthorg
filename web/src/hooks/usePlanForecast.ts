import { useEffect } from 'react'

import type { ForecastView } from '@/api/types/budget'
import { usePlanForecastStore } from '@/stores/planForecast'

export interface UsePlanForecastReturn {
  forecast: ForecastView | null
  loading: boolean
  error: string | null
}

/**
 * Hydrate the cost forecast a plan was released alongside, keyed by its
 * ``forecast_id``. Delegates the fetch to the plan-forecast store (matching
 * usePlanDetailData), and clears it on unmount / when the plan has no forecast.
 */
export function usePlanForecast(forecastId: string | null): UsePlanForecastReturn {
  const forecast = usePlanForecastStore((s) => s.forecast)
  const loading = usePlanForecastStore((s) => s.loading)
  const error = usePlanForecastStore((s) => s.error)

  useEffect(() => {
    if (forecastId === null) {
      usePlanForecastStore.getState().clear()
      return
    }
    void usePlanForecastStore.getState().fetchForecast(forecastId)
    return () => {
      usePlanForecastStore.getState().clear()
    }
  }, [forecastId])

  return { forecast, loading, error }
}
