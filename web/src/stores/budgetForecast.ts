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

export const useBudgetForecastStore = create<BudgetForecastState>((set) => ({
  current: null,
  loading: false,
  mutating: false,
  error: null,

  fetchForecast: async (forecastId: string) => {
    set({ loading: true, error: null })
    try {
      const forecast = await apiGetForecast(forecastId)
      set({ current: forecast, loading: false })
    } catch (err) {
      const message = getErrorMessage(err)
      log.warn('Fetch forecast failed', message)
      set({ loading: false, error: message })
    }
  },

  createForecast: async (data: ForecastRequest) => {
    set({ mutating: true })
    try {
      const forecast = await apiCreateForecast(data)
      set({ current: forecast, mutating: false })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Cost forecast generated',
      })
      return forecast
    } catch (err) {
      log.error('Create forecast failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to generate cost forecast'),
        description: getErrorMessage(err),
      })
      set({ mutating: false })
      return null
    }
  },

  approveForecast: async (forecastId: string, data: ForecastApproveRequest) => {
    set({ mutating: true })
    try {
      const forecast = await apiApproveForecast(forecastId, data)
      set({ current: forecast, mutating: false })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Forecast approved',
      })
      return forecast
    } catch (err) {
      log.error('Approve forecast failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to approve forecast'),
        description: getErrorMessage(err),
      })
      set({ mutating: false })
      return null
    }
  },

  rejectForecast: async (forecastId: string, data: ForecastRejectRequest) => {
    set({ mutating: true })
    try {
      const forecast = await apiRejectForecast(forecastId, data)
      set({ current: forecast, mutating: false })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Forecast rejected',
      })
      return forecast
    } catch (err) {
      log.error('Reject forecast failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to reject forecast'),
        description: getErrorMessage(err),
      })
      set({ mutating: false })
      return null
    }
  },

  raiseCeiling: async (forecastId: string, data: RaiseCeilingRequest) => {
    set({ mutating: true })
    try {
      const forecast = await apiRaiseCeiling(forecastId, data)
      set({ current: forecast, mutating: false })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Hard ceiling raised; run can resume',
      })
      return forecast
    } catch (err) {
      log.error('Raise ceiling failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to raise hard ceiling'),
        description: getErrorMessage(err),
      })
      set({ mutating: false })
      return null
    }
  },

  reset: () => {
    set({ current: null, loading: false, mutating: false, error: null })
  },
}))
