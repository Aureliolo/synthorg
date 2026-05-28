import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { useCollaborationStore } from '@/stores/collaboration'
import { useToastStore } from '@/stores/toast'
import { apiError, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'

const OVERRIDE_URL = '/api/v1/agents/:id/collaboration/override'

describe('useCollaborationStore', () => {
  beforeEach(() => {
    useToastStore.getState().dismissAll()
  })

  describe('getOverride', () => {
    it('returns the active override', async () => {
      const result = await useCollaborationStore.getState().getOverride('agent-1')
      expect(result.kind).toBe('ok')
    })

    it('treats a 404 as missing without toasting', async () => {
      server.use(
        http.get(OVERRIDE_URL, () =>
          HttpResponse.json(apiError('not found'), { status: 404 }),
        ),
      )

      const result = await useCollaborationStore.getState().getOverride('agent-1')

      expect(result.kind).toBe('missing')
      expect(useToastStore.getState().toasts).toHaveLength(0)
    })

    it('reports an error and toasts on a non-404 failure', async () => {
      server.use(
        http.get(OVERRIDE_URL, () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      const result = await useCollaborationStore.getState().getOverride('agent-1')

      expect(result.kind).toBe('error')
      expect(useToastStore.getState().toasts[0]!.variant).toBe('error')
    })
  })

  describe('clearOverride', () => {
    it('clears the override and toasts success', async () => {
      server.use(
        http.delete(OVERRIDE_URL, () => HttpResponse.json(voidSuccess())),
      )

      const ok = await useCollaborationStore.getState().clearOverride('agent-1')

      expect(ok).toBe(true)
      const toasts = useToastStore.getState().toasts
      expect(toasts[0]!.variant).toBe('success')
      expect(toasts[0]!.title).toBe('Collaboration override cleared')
    })

    it('returns false and toasts an error on failure', async () => {
      server.use(
        http.delete(OVERRIDE_URL, () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      const ok = await useCollaborationStore.getState().clearOverride('agent-1')

      expect(ok).toBe(false)
      const toasts = useToastStore.getState().toasts
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to clear collaboration override')
    })
  })
})
