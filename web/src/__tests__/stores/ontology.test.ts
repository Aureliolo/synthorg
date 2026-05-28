import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { listEntities, type EntityResponse } from '@/api/endpoints/ontology'
import { useOntologyStore } from '@/stores/ontology'
import { useToastStore } from '@/stores/toast'
import { apiError, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { PaginatedResult } from '@/api/client'
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

function singlePage(
  entities: readonly EntityResponse[],
): PaginatedResult<EntityResponse> {
  const limit = 200
  return {
    data: [...entities],
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: { limit, next_cursor: null, has_more: false },
  }
}

describe('useOntologyStore', () => {
  beforeEach(() => {
    useOntologyStore.setState({
      entities: [],
      totalEntities: 0,
      mutating: false,
      selectedEntity: null,
    })
    useToastStore.getState().dismissAll()
  })

  it('fetches entities and records the total', async () => {
    server.use(
      http.get('/api/v1/ontology/entities', () =>
        HttpResponse.json(
          paginatedFor<typeof listEntities>(singlePage([buildEntity()])),
        ),
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

    expect(useOntologyStore.getState().entitiesError).toBeTruthy()
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
    // The bare DELETE wrapper does not inspect the envelope (the backend
    // replies 204 No Content on success), so a failure must arrive as an
    // HTTP error status for axios to reject.
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
