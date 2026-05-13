import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { useToastStore } from '@/stores/toast'
import {
  apiError,
  apiSuccess,
  emptyPage,
  paginatedFor,
  voidSuccess,
} from '@/mocks/handlers'
import type { listSubworkflows } from '@/api/endpoints/subworkflows'
import type { SubworkflowSummary } from '@/api/types/workflows'
import { server } from '@/test-setup'

function resetStore() {
  useSubworkflowsStore.setState({
    subworkflows: [],
    listLoading: false,
    listError: null,
    searchQuery: '',
    subworkflowsTruncated: false,
  })
  useToastStore.getState().dismissAll()
}

function buildSummary(
  overrides: Partial<SubworkflowSummary> = {},
): SubworkflowSummary {
  return {
    subworkflow_id: 'sub-default',
    latest_version: '1.0.0',
    name: 'Default',
    description: '',
    input_count: 0,
    output_count: 0,
    version_count: 1,
    ...overrides,
  }
}

function pageOf(
  summaries: readonly SubworkflowSummary[],
  cursor: string | null = null,
): {
  data: SubworkflowSummary[]
  total: number | null
  offset: number
  limit: number
  nextCursor: string | null
  hasMore: boolean
  pagination: {
    total: number | null
    offset: number
    limit: number
    next_cursor: string | null
    has_more: boolean
  }
} {
  const limit = 100
  // Always ``null`` under the keyset wire contract: the backend skips
  // COUNT on every request and the dashboard derives display counts
  // from ``data.length``.
  const total = null
  return {
    data: [...summaries],
    total,
    offset: 0,
    limit,
    nextCursor: cursor,
    hasMore: cursor !== null,
    pagination: {
      total,
      offset: 0,
      limit,
      next_cursor: cursor,
      has_more: cursor !== null,
    },
  }
}

beforeEach(() => {
  resetStore()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('deleteSubworkflow', () => {
  it('refetches and emits a success toast on success', async () => {
    let refetched = 0
    server.use(
      http.delete('/api/v1/subworkflows/:id/versions/:version', () =>
        HttpResponse.json(voidSuccess()),
      ),
      http.get('/api/v1/subworkflows', () => {
        refetched += 1
        return HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(emptyPage<SubworkflowSummary>()),
        )
      }),
    )

    const result = await useSubworkflowsStore
      .getState()
      .deleteSubworkflow('swf-1', '2.0')

    expect(result).toBe(true)
    expect(refetched).toBe(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('success')
    expect(toasts[0]!.title).toBe('Subworkflow deleted')
  })

  it('returns false and emits an error toast on API failure', async () => {
    let refetched = 0
    server.use(
      http.delete('/api/v1/subworkflows/:id/versions/:version', () =>
        HttpResponse.json(apiError('boom')),
      ),
      http.get('/api/v1/subworkflows', () => {
        refetched += 1
        return HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(emptyPage<SubworkflowSummary>()),
        )
      }),
    )

    const result = await useSubworkflowsStore
      .getState()
      .deleteSubworkflow('swf-1', '2.0')

    expect(result).toBe(false)
    expect(refetched).toBe(0)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to delete subworkflow')
    expect(toasts[0]!.description).toBe('boom')
  })
})

describe('fetchSubworkflows', () => {
  it('populates subworkflows and clears error on success', async () => {
    server.use(
      http.get('/api/v1/subworkflows', () =>
        HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(emptyPage<SubworkflowSummary>()),
        ),
      ),
    )

    useSubworkflowsStore.setState({ listError: 'stale' })
    await useSubworkflowsStore.getState().fetchSubworkflows()

    const state = useSubworkflowsStore.getState()
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('uses searchSubworkflows when a search query is set', async () => {
    let searchCalls = 0
    let listCalls = 0
    let searchQuery: string | null = null
    server.use(
      http.get('/api/v1/subworkflows/search', ({ request }) => {
        searchCalls += 1
        searchQuery = new URL(request.url).searchParams.get('q')
        return HttpResponse.json(apiSuccess([]))
      }),
      http.get('/api/v1/subworkflows', () => {
        listCalls += 1
        return HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(emptyPage<SubworkflowSummary>()),
        )
      }),
    )

    useSubworkflowsStore.setState({ searchQuery: 'needle' })
    await useSubworkflowsStore.getState().fetchSubworkflows()

    expect(searchCalls).toBe(1)
    expect(searchQuery).toBe('needle')
    expect(listCalls).toBe(0)
  })

  it('drains cursored pages eagerly and concatenates the results', async () => {
    const subA = buildSummary({ subworkflow_id: 'sub-a' })
    const subB = buildSummary({ subworkflow_id: 'sub-b' })
    const subC = buildSummary({ subworkflow_id: 'sub-c' })
    const cursorSeen: string[] = []
    let call = 0
    server.use(
      http.get('/api/v1/subworkflows', ({ request }) => {
        const url = new URL(request.url)
        const cursor = url.searchParams.get('cursor')
        cursorSeen.push(cursor ?? 'first')
        call += 1
        if (call === 1) {
          return HttpResponse.json(
            paginatedFor<typeof listSubworkflows>(pageOf([subA, subB], 'cursor-2')),
          )
        }
        return HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(pageOf([subC])),
        )
      }),
    )
    await useSubworkflowsStore.getState().fetchSubworkflows()
    const state = useSubworkflowsStore.getState()
    expect(state.subworkflows.map((s) => s.subworkflow_id)).toEqual([
      'sub-a',
      'sub-b',
      'sub-c',
    ])
    expect(cursorSeen).toEqual(['first', 'cursor-2'])
  })

  it('sets subworkflowsTruncated when the eager drain hits MAX_PAGES with more pages remaining', async () => {
    let pageNum = 0
    server.use(
      http.get('/api/v1/subworkflows', () => {
        pageNum += 1
        // Always advertise another page so the loop runs the full
        // MAX_PAGES iterations and the final ``pageIndex === MAX_PAGES - 1``
        // branch marks the result as truncated.
        return HttpResponse.json(
          paginatedFor<typeof listSubworkflows>(
            pageOf([buildSummary({ subworkflow_id: `sub-${pageNum}` })], `cursor-${pageNum + 1}`),
          ),
        )
      }),
    )

    await useSubworkflowsStore.getState().fetchSubworkflows()

    const state = useSubworkflowsStore.getState()
    expect(state.subworkflowsTruncated).toBe(true)
    expect(state.subworkflows).toHaveLength(20)
  })

  it('sets listError on failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/subworkflows', () =>
        HttpResponse.json(apiError('network down')),
      ),
    )

    await useSubworkflowsStore.getState().fetchSubworkflows()

    const state = useSubworkflowsStore.getState()
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBe('network down')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
