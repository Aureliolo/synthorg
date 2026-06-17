import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { useRecommendationsStore } from '@/stores/recommendations'
import { server } from '@/test-setup'

const BASE = '/api/v1/providers/model-refresh'

function failJson(status: number) {
  return HttpResponse.json(
    { success: false, error: 'boom', error_detail: null, data: null },
    { status },
  )
}

describe('recommendations store', () => {
  beforeEach(() => {
    useRecommendationsStore.getState().reset()
  })

  it('fetchRecommendations populates pending recommendations', async () => {
    await useRecommendationsStore.getState().fetchRecommendations()
    const state = useRecommendationsStore.getState()
    expect(state.recommendations.length).toBeGreaterThan(0)
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBeNull()
  })

  it('fetchRecommendations records the error without throwing', async () => {
    server.use(http.get(`${BASE}/recommendations`, () => failJson(500)))
    await useRecommendationsStore.getState().fetchRecommendations()
    const state = useRecommendationsStore.getState()
    expect(state.listError).not.toBeNull()
    expect(state.listLoading).toBe(false)
  })

  it('approve removes the decided recommendation on success', async () => {
    await useRecommendationsStore.getState().fetchRecommendations()
    const id = useRecommendationsStore.getState().recommendations[0]!.id
    const ok = await useRecommendationsStore.getState().approve(id, 'operator')
    expect(ok).toBe(true)
    const state = useRecommendationsStore.getState()
    expect(state.recommendations.some((r) => r.id === id)).toBe(false)
    expect(state.decidingId).toBeNull()
  })

  it('approve keeps the recommendation on failure and returns false', async () => {
    await useRecommendationsStore.getState().fetchRecommendations()
    const before = useRecommendationsStore.getState().recommendations.length
    const id = useRecommendationsStore.getState().recommendations[0]!.id
    server.use(http.post(`${BASE}/recommendations/:id/approve`, () => failJson(409)))
    const ok = await useRecommendationsStore.getState().approve(id, 'operator')
    expect(ok).toBe(false)
    expect(useRecommendationsStore.getState().recommendations.length).toBe(before)
    expect(useRecommendationsStore.getState().decidingId).toBeNull()
  })

  it('reject removes the decided recommendation on success', async () => {
    await useRecommendationsStore.getState().fetchRecommendations()
    const id = useRecommendationsStore.getState().recommendations[0]!.id
    const ok = await useRecommendationsStore.getState().reject(id, 'operator')
    expect(ok).toBe(true)
    expect(useRecommendationsStore.getState().recommendations.some((r) => r.id === id)).toBe(
      false,
    )
  })

  it('runRefresh refetches the list and clears the refreshing flag', async () => {
    const ok = await useRecommendationsStore.getState().runRefresh()
    expect(ok).toBe(true)
    expect(useRecommendationsStore.getState().refreshing).toBe(false)
    expect(useRecommendationsStore.getState().recommendations.length).toBeGreaterThan(0)
  })

  it('fetchStatus populates the refresh status', async () => {
    await useRecommendationsStore.getState().fetchStatus()
    expect(useRecommendationsStore.getState().status).not.toBeNull()
  })
})
