import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { usePromotionStore } from '@/stores/promotion'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess, buildPromotionEvaluation, buildPromotionRecord } from '@/mocks/handlers'
import { server } from '@/test-setup'

beforeEach(() => {
  usePromotionStore.getState().reset()
  useToastStore.getState().dismissAll()
})

afterEach(() => {
  usePromotionStore.getState().reset()
})

describe('promotion store', () => {
  it('stores the evaluation on success', async () => {
    server.use(
      http.get('/api/v1/promotion/:id/evaluate', () =>
        HttpResponse.json(apiSuccess(buildPromotionEvaluation({ eligible: true }))),
      ),
    )

    await usePromotionStore.getState().evaluate('agent-1', 'promotion')

    expect(usePromotionStore.getState().evaluation?.eligible).toBe(true)
    expect(usePromotionStore.getState().evaluationError).toBeNull()
  })

  it('sets evaluationError without toasting on a failed evaluation', async () => {
    server.use(
      http.get('/api/v1/promotion/:id/evaluate', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )

    await usePromotionStore.getState().evaluate('agent-1', 'promotion')

    expect(usePromotionStore.getState().evaluationError).not.toBeNull()
    expect(usePromotionStore.getState().evaluation).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('emits a success toast and refreshes history on apply', async () => {
    server.use(
      http.post('/api/v1/promotion/:id/apply', () =>
        HttpResponse.json(
          apiSuccess({
            applied: buildPromotionRecord({ agent_id: 'agent-1' }),
            request: {
              id: 'req-1',
              agent_id: 'agent-1',
              agent_name: 'Dana',
              approval_id: null,
              created_at: '2026-06-15T09:00:00+00:00',
              current_level: 'mid',
              direction: 'promotion',
              status: 'approved',
              target_level: 'senior',
            },
          }),
        ),
      ),
      http.get('/api/v1/promotion/:id/history', () =>
        HttpResponse.json(apiSuccess([buildPromotionRecord({ agent_id: 'agent-1' })])),
      ),
    )

    const result = await usePromotionStore.getState().apply('agent-1', 'promotion')

    expect(result?.applied).not.toBeNull()
    expect(usePromotionStore.getState().history).toHaveLength(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]?.variant).toBe('success')
  })

  it('returns null and emits an error toast when apply fails', async () => {
    server.use(
      http.post('/api/v1/promotion/:id/apply', () =>
        HttpResponse.json(apiError('forbidden'), { status: 403 }),
      ),
    )

    const result = await usePromotionStore.getState().apply('agent-1', 'demotion')

    expect(result).toBeNull()
    expect(useToastStore.getState().toasts[0]?.variant).toBe('error')
  })

  it('stores cycle results and toasts on a successful cycle run', async () => {
    server.use(
      http.post('/api/v1/promotion/cycle', () =>
        HttpResponse.json(apiSuccess([buildPromotionRecord(), buildPromotionRecord({ id: 'promo-2' })])),
      ),
    )

    const ok = await usePromotionStore.getState().runCycle()

    expect(ok).toBe(true)
    expect(usePromotionStore.getState().cycleResult).toHaveLength(2)
    expect(useToastStore.getState().toasts[0]?.variant).toBe('success')
  })
})
