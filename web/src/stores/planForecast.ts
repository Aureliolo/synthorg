import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { getForecast } from '@/api/endpoints/budget'
import type { Forecast } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-forecast')

// Monotonic request token: a plan-navigation change can leave an older fetch
// in flight, and its late resolve must not clobber the current plan's forecast.
let requestToken = 0

export interface PlanForecastState {
  forecast: Forecast | null
  loading: boolean
  error: string | null
  fetchForecast: (forecastId: string) => Promise<void>
  clear: () => void
}

type PfSet = StoreApi<PlanForecastState>['setState']

async function fetchForecastImpl(set: PfSet, forecastId: string): Promise<void> {
  const token = (requestToken += 1)
  set({ loading: true, error: null, forecast: null })
  try {
    const forecast = await getForecast(forecastId)
    if (token !== requestToken) return
    set({ forecast, loading: false })
  } catch (err) {
    if (token !== requestToken) return
    const message = getErrorMessage(err)
    log.warn('Fetch plan forecast failed', sanitizeForLog(err))
    set({ loading: false, error: message })
  }
}

/**
 * The cost forecast a plan was released alongside. A dedicated per-view store
 * (not the shared budget-forecast singleton) so the plan workspace never
 * renders another view's ``current`` forecast; still a pure API consumer
 * (re-hydrated from the backend on mount, nothing persisted).
 */
export const usePlanForecastStore = create<PlanForecastState>((set) => ({
  forecast: null,
  loading: false,
  error: null,
  fetchForecast: (forecastId) => fetchForecastImpl(set, forecastId),
  clear: () => {
    requestToken += 1
    set({ forecast: null, loading: false, error: null })
  },
}))
