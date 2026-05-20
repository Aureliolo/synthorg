import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { useBudgetForecastStore } from '@/stores/budgetForecast'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

describe('budgetForecast store', () => {
  beforeEach(() => {
    useBudgetForecastStore.getState().reset()
  })

  it('fetchForecast populates state on success', async () => {
    await useBudgetForecastStore.getState().fetchForecast(
      '00000000-0000-0000-0000-000000000001',
    )
    const state = useBudgetForecastStore.getState()
    expect(state.current).not.toBeNull()
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('fetchForecast records the error on failure without throwing', async () => {
    server.use(
      http.get('/api/v1/budget/forecasts/:forecastId', () =>
        HttpResponse.json(
          { success: false, error: 'boom', error_detail: null, data: null },
          { status: 500 },
        ),
      ),
    )
    await useBudgetForecastStore.getState().fetchForecast(
      '00000000-0000-0000-0000-000000000001',
    )
    const state = useBudgetForecastStore.getState()
    expect(state.error).not.toBeNull()
    expect(state.loading).toBe(false)
  })

  it('approveForecast updates state and toasts on success', async () => {
    const result = await useBudgetForecastStore.getState().approveForecast(
      '00000000-0000-0000-0000-000000000001',
      { decided_by: 'operator', ceiling_amount: 1.8 },
    )
    expect(result).not.toBeNull()
    expect(result?.decision).toBe('approved')
    expect(useBudgetForecastStore.getState().mutating).toBe(false)
  })

  it('approveForecast returns null sentinel + error toast on failure', async () => {
    server.use(
      http.post('/api/v1/budget/forecasts/:forecastId/approve', () =>
        HttpResponse.json(
          { success: false, error: 'forbidden', error_detail: null, data: null },
          { status: 403 },
        ),
      ),
    )
    const before = useToastStore.getState().toasts.length
    const result = await useBudgetForecastStore.getState().approveForecast(
      '00000000-0000-0000-0000-000000000001',
      { decided_by: 'operator', ceiling_amount: null },
    )
    expect(result).toBeNull()
    expect(useBudgetForecastStore.getState().mutating).toBe(false)
    const after = useToastStore.getState().toasts.length
    expect(after).toBeGreaterThan(before)
  })

  it('rejectForecast updates state on success', async () => {
    const result = await useBudgetForecastStore.getState().rejectForecast(
      '00000000-0000-0000-0000-000000000001',
      { decided_by: 'operator' },
    )
    expect(result?.decision).toBe('rejected')
  })

  it('raiseCeiling updates state on success', async () => {
    const result = await useBudgetForecastStore.getState().raiseCeiling(
      '00000000-0000-0000-0000-000000000001',
      { new_ceiling: 2.5, accumulated_cost: 1.2 },
    )
    expect(result?.ceiling_amount).toBe(2.5)
  })

  it('reset clears every field', async () => {
    await useBudgetForecastStore.getState().fetchForecast(
      '00000000-0000-0000-0000-000000000001',
    )
    useBudgetForecastStore.getState().reset()
    const state = useBudgetForecastStore.getState()
    expect(state.current).toBeNull()
    expect(state.error).toBeNull()
    expect(state.loading).toBe(false)
    expect(state.mutating).toBe(false)
  })
})
