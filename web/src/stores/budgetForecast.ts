import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  approveForecast as apiApproveForecast,
  createForecast as apiCreateForecast,
  getForecast as apiGetForecast,
  raiseCeiling as apiRaiseCeiling,
  rejectForecast as apiRejectForecast,
} from '@/api/endpoints/budget'
import type {
  Forecast,
  ForecastRequest,
  ForecastApproveRequest,
  ForecastRejectRequest,
  RaiseCeilingRequest,
} from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('budget-forecast-store')

export interface BudgetForecastState {
  current: Forecast | null
  loading: boolean
  mutating: boolean
  error: string | null
  fetchForecast: (forecastId: string) => Promise<void>
  createForecast: (data: ForecastRequest) => Promise<Forecast | null>
  approveForecast: (
    forecastId: string,
    data: ForecastApproveRequest,
  ) => Promise<Forecast | null>
  rejectForecast: (
    forecastId: string,
    data: ForecastRejectRequest,
  ) => Promise<Forecast | null>
  raiseCeiling: (
    forecastId: string,
    data: RaiseCeilingRequest,
  ) => Promise<Forecast | null>
  reset: () => void
}

type BfSet = StoreApi<BudgetForecastState>['setState']

async function mutateForecast(
  set: BfSet,
  call: () => Promise<Forecast>,
  successTitle: string,
  fallbackTitle: string,
  logPrefix: string,
): Promise<Forecast | null> {
  set({ mutating: true })
  try {
    const forecast = await call()
    set({ current: forecast, mutating: false })
    useToastStore.getState().add({ variant: 'success', title: successTitle })
    return forecast
  } catch (err) {
    log.error(`${logPrefix}:`, getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, fallbackTitle),
      description: getErrorMessage(err),
    })
    set({ mutating: false })
    return null
  }
}

async function fetchForecastImpl(
  set: BfSet,
  forecastId: string,
): Promise<void> {
  set({ loading: true, error: null })
  try {
    const forecast = await apiGetForecast(forecastId)
    set({ current: forecast, loading: false })
  } catch (err) {
    const message = getErrorMessage(err)
    log.warn('Fetch forecast failed', message)
    set({ loading: false, error: message })
  }
}

export const useBudgetForecastStore = create<BudgetForecastState>((set) => ({
  current: null,
  loading: false,
  mutating: false,
  error: null,

  fetchForecast: (forecastId) => fetchForecastImpl(set, forecastId),
  createForecast: (data) =>
    mutateForecast(
      set,
      () => apiCreateForecast(data),
      'Cost forecast generated',
      'Failed to generate cost forecast',
      'Create forecast failed',
    ),
  approveForecast: (forecastId, data) =>
    mutateForecast(
      set,
      () => apiApproveForecast(forecastId, data),
      'Forecast approved',
      'Failed to approve forecast',
      'Approve forecast failed',
    ),
  rejectForecast: (forecastId, data) =>
    mutateForecast(
      set,
      () => apiRejectForecast(forecastId, data),
      'Forecast rejected',
      'Failed to reject forecast',
      'Reject forecast failed',
    ),
  raiseCeiling: (forecastId, data) =>
    mutateForecast(
      set,
      () => apiRaiseCeiling(forecastId, data),
      'Hard ceiling raised; run can resume',
      'Failed to raise hard ceiling',
      'Raise ceiling failed',
    ),
  reset: () => {
    set({ current: null, loading: false, mutating: false, error: null })
  },
}))
