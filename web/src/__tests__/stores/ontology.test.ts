import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { EntityListResponse, EntityResponse } from '@/api/types/ontology'
import { useOntologyStore } from '@/stores/ontology'
import { useToastStore } from '@/stores/toast'
import { apiError, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'

function buildEntity(overrides: Partial<EntityResponse> = {}): EntityResponse {
  return {
    name: 'Task',
    tier: 'user',
    source: 'api',
    definition: 'A unit of work',
    fields: [],
    constraints: [],
    disambiguation: '',
    relationships: [],
    created_by: 'user-1',
    created_at: '2026-04-19T00:00:00Z',
    updated_at: '2026-04-19T00:00:00Z',
    ...overrides,
  }
}

function singlePageResponse(
  entities: readonly EntityResponse[],
): EntityListResponse {
  const userCount = entities.filter((e) => e.tier === 'user').length
  return {
    data: [...entities],
    error: null,
    error_detail: null,
    pagination: { limit: 200, next_cursor: null, has_more: false },
    success: true,
    degraded_sources: [],
    meta: {
      core_count: entities.length - userCount,
      user_count: userCount,
      total_count: entities.length,
      drift_summary: null,
    },
  }
}

describe('useOntologyStore', () => {
  beforeEach(() => {
    // Zustand setState is a shallow merge, so reset the FULL slice: a partial
    // reset leaves entitiesLoading / entitiesError / drift state dirty and
    // makes randomised-order tests non-deterministic.
    useOntologyStore.setState({
      entities: [],
      totalEntities: 0,
      entityMeta: null,
      entitiesLoading: false,
      entitiesError: null,
      driftReports: [],
      driftLoading: false,
      driftError: null,
      tierFilter: 'all',
      searchQuery: '',
      entitySortBy: 'name',
      entitySortDirection: 'asc',
      selectedEntity: null,
      mutating: false,
    })
    useToastStore.getState().dismissAll()
  })

  it('fetches entities and records the total', async () => {
    server.use(
      http.get('/api/v1/ontology/entities', () =>
        HttpResponse.json(singlePageResponse([buildEntity()])),
      ),
    )

    await useOntologyStore.getState().fetchEntities()

    const state = useOntologyStore.getState()
    expect(state.entities).toHaveLength(1)
    expect(state.totalEntities).toBe(1)
    expect(state.entitiesLoading).toBe(false)
  })

  it('records an error message when the entity list call fails', async () => {
    server.use(
      http.get('/api/v1/ontology/entities', () =>
        HttpResponse.json(apiError('Network down'), { status: 500 }),
      ),
    )

    await useOntologyStore.getState().fetchEntities()

    // A 500 now surfaces the backend's real (secret-redacted) error rather
    // than a generic placeholder.
    expect(useOntologyStore.getState().entitiesError).toContain('Network down')
    expect(useOntologyStore.getState().entitiesLoading).toBe(false)
  })

  it('optimistically removes an entity on delete and keeps it removed on success', async () => {
    const entity = buildEntity({ name: 'Task' })
    useOntologyStore.setState({ entities: [entity], totalEntities: 1 })
    server.use(
      http.delete('/api/v1/ontology/entities/:name', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )

    const result = await useOntologyStore.getState().deleteEntity('Task')

    expect(result).toBe(true)
    const state = useOntologyStore.getState()
    expect(state.entities).toHaveLength(0)
    expect(state.totalEntities).toBe(0)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('success')
  })

  it('clears the selected entity when it is the one deleted', async () => {
    const entity = buildEntity({ name: 'Task' })
    useOntologyStore.setState({
      entities: [entity],
      totalEntities: 1,
      selectedEntity: entity,
    })
    server.use(
      http.delete('/api/v1/ontology/entities/:name', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )

    await useOntologyStore.getState().deleteEntity('Task')

    expect(useOntologyStore.getState().selectedEntity).toBeNull()
  })

  it('rolls back state and surfaces an error toast on delete failure', async () => {
    const entity = buildEntity({ name: 'Task' })
    useOntologyStore.setState({ entities: [entity], totalEntities: 1 })
    // deleteEntity validates the envelope via unwrapVoid, but the backend
    // replies 204 No Content on success (no body to inspect), so a failure
    // must arrive as an HTTP error status for axios to reject.
    server.use(
      http.delete('/api/v1/ontology/entities/:name', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )

    const result = await useOntologyStore.getState().deleteEntity('Task')

    expect(result).toBe(false)
    const state = useOntologyStore.getState()
    expect(state.entities).toHaveLength(1)
    expect(state.totalEntities).toBe(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to delete entity')
  })
})
