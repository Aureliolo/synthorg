import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { apiError } from '@/mocks/handlers'
import { useMissionControlStore } from '@/stores/mission-control'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

afterEach(() => {
  useToastStore.getState().dismissAll()
  useMissionControlStore.setState({
    snapshot: null,
    snapshotLoading: false,
    snapshotError: null,
    frames: [],
    framesExecutionId: null,
    framesLoading: false,
    framesError: null,
    seekView: null,
  })
})

describe('useMissionControlStore', () => {
  it('fetchSnapshot stores the live snapshot', async () => {
    await useMissionControlStore.getState().fetchSnapshot()
    expect(useMissionControlStore.getState().snapshot).not.toBeNull()
    expect(useMissionControlStore.getState().snapshotError).toBeNull()
  })

  it('pauseTaskAction returns the task and emits a success toast', async () => {
    const task = await useMissionControlStore.getState().pauseTaskAction('t1', 'why')
    expect(task).not.toBeNull()
    expect(useToastStore.getState().toasts.some((t) => t.variant === 'success')).toBe(
      true,
    )
  })

  it('pauseTaskAction returns null and toasts on error', async () => {
    server.use(
      http.post('/api/v1/cockpit/interventions/pause', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    const result = await useMissionControlStore.getState().pauseTaskAction('t1', 'why')
    expect(result).toBeNull()
    expect(useToastStore.getState().toasts.some((t) => t.variant === 'error')).toBe(true)
  })
})
