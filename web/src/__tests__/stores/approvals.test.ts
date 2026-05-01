import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useApprovalsStore, _resetPendingTransitions } from '@/stores/approvals'
import { useToastStore } from '@/stores/toast'
import { makeApproval } from '../helpers/factories'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import type { listApprovals } from '@/api/endpoints/approvals'
import { server } from '@/test-setup'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { WsEvent } from '@/api/types/websocket'

// Bidi-override chars constructed via fromCharCode so ESLint's
// ``security/detect-bidi-characters`` rule sees only hex in source.
const RLO = String.fromCharCode(0x202e)
const LRO = String.fromCharCode(0x202d)

function paginated(
  data: ApprovalResponse[],
  meta: Partial<{ total: number | null; offset: number; limit: number }> = {},
) {
  // ``total`` is ``number | null`` under cursor pagination so the
  // repo-backed ``total === null`` path (where the backend skips the
  // COUNT(*) round-trip) is exercisable from tests. ``'total' in
  // meta`` means the caller explicitly wanted the supplied value
  // (possibly ``null``); otherwise fall back to ``data.length``.
  const limit = meta.limit ?? 200
  return paginatedFor<typeof listApprovals>({
    data,
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit,
      next_cursor: null,
      has_more: false,
    },
  })
}

function resetStore() {
  _resetPendingTransitions()
  useApprovalsStore.setState({
    approvals: [],
    selectedApproval: null,
    loading: false,
    loadingDetail: false,
    error: null,
    detailError: null,
    selectedIds: new Set(),
  })
}

beforeEach(() => {
  resetStore()
  useToastStore.getState().dismissAll()
})

afterEach(() => {
  useToastStore.getState().dismissAll()
  vi.restoreAllMocks()
})

describe('fetchApprovals', () => {
  it('sets loading and stores results', async () => {
    const items = [makeApproval('1'), makeApproval('2')]
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(paginated(items, { total: 2 })),
      ),
    )

    await useApprovalsStore.getState().fetchApprovals()

    const state = useApprovalsStore.getState()
    expect(state.loading).toBe(false)
    expect(state.approvals).toHaveLength(2)
    expect(state.total).toBe(2)
    expect(state.error).toBeNull()
  })

  it('sets error on failure', async () => {
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(apiError('Network error')),
      ),
    )

    await useApprovalsStore.getState().fetchApprovals()

    const state = useApprovalsStore.getState()
    expect(state.loading).toBe(false)
    expect(state.error).toBe('Network error')
  })

  it('forwards filters as query params', async () => {
    const captured: { params: URLSearchParams | null } = { params: null }
    server.use(
      http.get('/api/v1/approvals', ({ request }) => {
        captured.params = new URL(request.url).searchParams
        return HttpResponse.json(paginated([]))
      }),
    )

    await useApprovalsStore
      .getState()
      .fetchApprovals({ status: 'pending', limit: 50 })

    expect(captured.params?.get('status')).toBe('pending')
    expect(captured.params?.get('limit')).toBe('50')
  })

  it('preserves optimistic state for items in pendingTransitions', async () => {
    const item = makeApproval('1')
    useApprovalsStore.setState({ approvals: [item] })

    useApprovalsStore.getState().optimisticApprove('1')
    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')

    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(
          paginated([makeApproval('1', { status: 'pending' })], { total: 1 }),
        ),
      ),
    )
    await useApprovalsStore.getState().fetchApprovals()

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')
  })
})

describe('fetchApproval', () => {
  it('sets loadingDetail and stores selected approval', async () => {
    const approval = makeApproval('1')
    server.use(
      http.get('/api/v1/approvals/:id', () =>
        HttpResponse.json(apiSuccess(approval)),
      ),
    )

    await useApprovalsStore.getState().fetchApproval('1')

    const state = useApprovalsStore.getState()
    expect(state.loadingDetail).toBe(false)
    expect(state.selectedApproval).toEqual(approval)
  })

  it('sets error on failure', async () => {
    server.use(
      http.get('/api/v1/approvals/:id', () =>
        HttpResponse.json(apiError('Not found')),
      ),
    )

    await useApprovalsStore.getState().fetchApproval('999')

    expect(useApprovalsStore.getState().loadingDetail).toBe(false)
    expect(useApprovalsStore.getState().detailError).toBe('Not found')
  })
})

describe('approveOne', () => {
  it('calls API and upserts result', async () => {
    const original = makeApproval('1', { status: 'pending' })
    const approved = makeApproval('1', {
      status: 'approved',
      decided_by: 'user',
      decided_at: '2026-03-27T12:00:00Z',
    })
    useApprovalsStore.setState({ approvals: [original] })

    let capturedBody: unknown = null
    server.use(
      http.post('/api/v1/approvals/:id/approve', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json(apiSuccess(approved))
      }),
    )

    const result = await useApprovalsStore
      .getState()
      .approveOne('1', { comment: 'LGTM' })

    expect(result).not.toBeNull()
    expect(result!.status).toBe('approved')
    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')
    expect(capturedBody).toEqual({ comment: 'LGTM' })
  })

  it('returns null and emits an error toast when API fails', async () => {
    server.use(
      http.post('/api/v1/approvals/:id/approve', () =>
        HttpResponse.json(apiError('Server error')),
      ),
    )

    const result = await useApprovalsStore.getState().approveOne('1')
    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Could not approve')
    expect(toasts[0]!.description).toBe('Server error')
  })
})

describe('rejectOne', () => {
  it('calls API and upserts result', async () => {
    const original = makeApproval('1', { status: 'pending' })
    const rejected = makeApproval('1', {
      status: 'rejected',
      decision_reason: 'Too risky',
    })
    useApprovalsStore.setState({ approvals: [original] })

    let capturedBody: unknown = null
    server.use(
      http.post('/api/v1/approvals/:id/reject', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json(apiSuccess(rejected))
      }),
    )

    const result = await useApprovalsStore
      .getState()
      .rejectOne('1', { reason: 'Too risky' })

    expect(result).not.toBeNull()
    expect(result!.status).toBe('rejected')
    expect(capturedBody).toEqual({ reason: 'Too risky' })
  })

  it('returns null and emits an error toast when API fails', async () => {
    server.use(
      http.post('/api/v1/approvals/:id/reject', () =>
        HttpResponse.json(apiError('Server error')),
      ),
    )

    const result = await useApprovalsStore.getState().rejectOne('1', { reason: 'x' })
    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Could not reject')
    expect(toasts[0]!.description).toBe('Server error')
  })
})

describe('upsertApproval', () => {
  it('prepends new approval', () => {
    const existing = makeApproval('1')
    useApprovalsStore.setState({ approvals: [existing], total: 1 })

    const newApproval = makeApproval('2')
    useApprovalsStore.getState().upsertApproval(newApproval)

    const state = useApprovalsStore.getState()
    expect(state.approvals).toHaveLength(2)
    expect(state.approvals[0]!.id).toBe('2')
    expect(state.total).toBe(2)
  })

  it('replaces existing approval', () => {
    const original = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({ approvals: [original], total: 1 })

    const updated = makeApproval('1', { status: 'approved' })
    useApprovalsStore.getState().upsertApproval(updated)

    const state = useApprovalsStore.getState()
    expect(state.approvals).toHaveLength(1)
    expect(state.approvals[0]!.status).toBe('approved')
    expect(state.total).toBe(1)
  })

  it('updates selectedApproval when matching', () => {
    const approval = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({
      approvals: [approval],
      selectedApproval: approval,
    })

    const updated = makeApproval('1', { status: 'approved' })
    useApprovalsStore.getState().upsertApproval(updated)

    expect(useApprovalsStore.getState().selectedApproval!.status).toBe('approved')
  })

  it('prunes selectedIds when approval leaves pending', () => {
    const approval = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({
      approvals: [approval],
      selectedIds: new Set(['1', '2']),
    })

    const decided = makeApproval('1', { status: 'approved' })
    useApprovalsStore.getState().upsertApproval(decided)

    const ids = useApprovalsStore.getState().selectedIds
    expect(ids.has('1')).toBe(false)
    expect(ids.has('2')).toBe(true)
  })

  it('does not update selectedApproval when not matching', () => {
    const selected = makeApproval('1')
    useApprovalsStore.setState({
      approvals: [selected],
      selectedApproval: selected,
    })

    const other = makeApproval('2')
    useApprovalsStore.getState().upsertApproval(other)

    expect(useApprovalsStore.getState().selectedApproval!.id).toBe('1')
  })
})

describe('optimisticApprove', () => {
  it('optimistically updates status and returns rollback', () => {
    const approval = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({ approvals: [approval] })

    const rollback = useApprovalsStore.getState().optimisticApprove('1')

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')

    rollback()

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('pending')
  })

  it('returns no-op for missing approval', () => {
    useApprovalsStore.setState({ approvals: [] })

    const rollback = useApprovalsStore.getState().optimisticApprove('nonexistent')

    expect(typeof rollback).toBe('function')
    rollback()
  })
})

describe('optimisticReject', () => {
  it('optimistically updates status and returns rollback', () => {
    const approval = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({ approvals: [approval] })

    const rollback = useApprovalsStore.getState().optimisticReject('1')

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('rejected')

    rollback()

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('pending')
  })
})

describe('handleWsEvent', () => {
  function makeWsEvent(approval: Partial<ApprovalResponse>): WsEvent {
    return {
      event_type: 'approval.submitted',
      channel: 'approvals',
      timestamp: '2026-03-27T12:00:00Z',
      payload: { approval },
    }
  }

  it('upserts valid approval from WS event', () => {
    useApprovalsStore.setState({ approvals: [] })

    const event = makeWsEvent(makeApproval('ws-1'))
    useApprovalsStore.getState().handleWsEvent(event)

    expect(useApprovalsStore.getState().approvals).toHaveLength(1)
    expect(useApprovalsStore.getState().approvals[0]!.id).toBe('ws-1')
  })

  it('ignores events without approval payload', () => {
    useApprovalsStore.setState({ approvals: [] })

    const event: WsEvent = {
      event_type: 'approval.submitted',
      channel: 'approvals',
      timestamp: '2026-03-27T12:00:00Z',
      payload: {},
    }
    useApprovalsStore.getState().handleWsEvent(event)

    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
  })

  it('ignores approval with missing required fields', () => {
    useApprovalsStore.setState({ approvals: [] })

    const event = makeWsEvent({ id: 'x' } as Partial<ApprovalResponse>)
    useApprovalsStore.getState().handleWsEvent(event)

    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
  })

  it('ignores array-typed approval payload', () => {
    useApprovalsStore.setState({ approvals: [] })

    const event: WsEvent = {
      event_type: 'approval.submitted',
      channel: 'approvals',
      timestamp: '2026-03-27T12:00:00Z',
      payload: { approval: [makeApproval('1')] as unknown },
    }
    useApprovalsStore.getState().handleWsEvent(event)

    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
  })

  it('skips when approval is in pendingTransitions', () => {
    const approval = makeApproval('1', { status: 'pending' })
    useApprovalsStore.setState({ approvals: [approval] })

    useApprovalsStore.getState().optimisticApprove('1')
    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')

    const event = makeWsEvent(makeApproval('1', { status: 'pending' }))
    useApprovalsStore.getState().handleWsEvent(event)

    expect(useApprovalsStore.getState().approvals[0]!.status).toBe('approved')
  })

  it('rejects approval whose metadata holds a non-string value', () => {
    useApprovalsStore.setState({ approvals: [] })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const event = makeWsEvent(
      makeApproval('bad-meta', {
        // Non-string metadata value violates the Record<string, string>
        // contract; ``isApprovalShape`` must reject.
        metadata: { region: 42 as unknown as string },
      }),
    )
    useApprovalsStore.getState().handleWsEvent(event)
    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
    errorSpy.mockRestore()
  })

  it('skips upsert when sanitized id collapses to empty', () => {
    useApprovalsStore.setState({ approvals: [] })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const event = makeWsEvent(makeApproval(RLO + LRO))
    useApprovalsStore.getState().handleWsEvent(event)
    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
    errorSpy.mockRestore()
  })

  it('sanitizes nullable fields to null when sanitization blanks them', () => {
    useApprovalsStore.setState({ approvals: [] })
    const event = makeWsEvent(
      makeApproval('null-decide', {
        status: 'approved',
        // All-bidi-override decided_by should collapse to null per the
        // string | null contract.
        decided_by: RLO,
      }),
    )
    useApprovalsStore.getState().handleWsEvent(event)
    const stored = useApprovalsStore.getState().approvals[0]
    expect(stored?.decided_by).toBeNull()
  })

  it('rejects approval whose id is mutated by sanitization', () => {
    useApprovalsStore.setState({ approvals: [] })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    // A legitimate-looking id with an embedded bidi override:
    // sanitization strips the override, producing a different effective
    // id that could alias a real approval. Reject the whole frame.
    const event = makeWsEvent(makeApproval(`approval-1${RLO}`))
    useApprovalsStore.getState().handleWsEvent(event)
    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
    errorSpy.mockRestore()
  })

  it('sanitizes and accepts approval with a valid evidence_package', () => {
    useApprovalsStore.setState({ approvals: [] })
    const event = makeWsEvent(
      makeApproval('with-evidence', {
        evidence_package: {
          id: 'ev-1',
          title: 'Evidence Title',
          narrative: 'Narrative body',
          reasoning_trace: ['step 1', 'step 2'],
          recommended_actions: [
            {
              action_type: 'approve',
              label: 'Approve',
              description: 'Approve the change',
              confirmation_required: true,
            },
          ],
          source_agent_id: 'agent-eng',
          task_id: null,
          risk_level: 'medium',
          metadata: { region: 'eu-west' },
          signature_threshold: 1,
          signatures: [
            {
              approver_id: 'agent-cto',
              algorithm: 'ed25519',
              signature_bytes: 'aGVsbG8=',
              signed_at: '2026-04-21T00:00:00Z',
              chain_position: 1,
            },
          ],
          is_fully_signed: true,
          created_at: '2026-04-21T00:00:00Z',
        },
      }),
    )
    useApprovalsStore.getState().handleWsEvent(event)
    const stored = useApprovalsStore.getState().approvals[0]
    expect(stored?.evidence_package?.title).toBe('Evidence Title')
    expect(stored?.evidence_package?.signatures).toHaveLength(1)
  })

  it('rejects approval whose evidence metadata contains non-string values', () => {
    useApprovalsStore.setState({ approvals: [] })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const event = makeWsEvent(
      makeApproval('bad-evidence', {
        evidence_package: {
          id: 'ev-2',
          title: 't',
          narrative: 'n',
          reasoning_trace: [],
          recommended_actions: [],
          source_agent_id: 'agent-eng',
          task_id: null,
          risk_level: 'medium',
          // Non-string value violates the string-string map guard.
          metadata: { count: 42 as unknown as string },
          signature_threshold: 1,
          signatures: [],
          is_fully_signed: false,
          created_at: '2026-04-21T00:00:00Z',
        },
      }),
    )
    useApprovalsStore.getState().handleWsEvent(event)
    expect(useApprovalsStore.getState().approvals).toHaveLength(0)
    errorSpy.mockRestore()
  })

  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['negative', -1],
    ['fractional', 0.5],
  ])(
    'rejects evidence signature with %s chain_position',
    (_label, bad) => {
      useApprovalsStore.setState({ approvals: [] })
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const event = makeWsEvent(
        makeApproval('bad-chain-pos', {
          evidence_package: {
            id: 'ev-cp',
            title: 't',
            narrative: 'n',
            reasoning_trace: [],
            recommended_actions: [],
            source_agent_id: 'agent-eng',
            task_id: null,
            risk_level: 'medium',
            metadata: {},
            signature_threshold: 1,
            signatures: [
              {
                approver_id: 'agent-cto',
                algorithm: 'ed25519',
                signature_bytes: 'aGVsbG8=',
                signed_at: '2026-04-21T00:00:00Z',
                chain_position: bad,
              },
            ],
            is_fully_signed: false,
            created_at: '2026-04-21T00:00:00Z',
          },
        }),
      )
      useApprovalsStore.getState().handleWsEvent(event)
      expect(useApprovalsStore.getState().approvals).toHaveLength(0)
      errorSpy.mockRestore()
    },
  )

  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['negative', -1],
    ['fractional', 0.5],
  ])(
    'rejects evidence with %s signature_threshold',
    (_label, bad) => {
      useApprovalsStore.setState({ approvals: [] })
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const event = makeWsEvent(
        makeApproval('bad-threshold', {
          evidence_package: {
            id: 'ev-th',
            title: 't',
            narrative: 'n',
            reasoning_trace: [],
            recommended_actions: [],
            source_agent_id: 'agent-eng',
            task_id: null,
            risk_level: 'medium',
            metadata: {},
            signature_threshold: bad,
            signatures: [],
            is_fully_signed: false,
            created_at: '2026-04-21T00:00:00Z',
          },
        }),
      )
      useApprovalsStore.getState().handleWsEvent(event)
      expect(useApprovalsStore.getState().approvals).toHaveLength(0)
      errorSpy.mockRestore()
    },
  )
})

describe('batch selection', () => {
  it('toggleSelection adds and removes', () => {
    useApprovalsStore.getState().toggleSelection('1')
    expect(useApprovalsStore.getState().selectedIds.has('1')).toBe(true)

    useApprovalsStore.getState().toggleSelection('1')
    expect(useApprovalsStore.getState().selectedIds.has('1')).toBe(false)
  })

  it('selectAllInGroup adds all IDs', () => {
    useApprovalsStore.getState().selectAllInGroup(['1', '2', '3'])
    expect(useApprovalsStore.getState().selectedIds.size).toBe(3)
  })

  it('deselectAllInGroup removes group IDs', () => {
    useApprovalsStore.getState().selectAllInGroup(['1', '2', '3', '4'])
    useApprovalsStore.getState().deselectAllInGroup(['2', '3'])
    const ids = useApprovalsStore.getState().selectedIds
    expect(ids.size).toBe(2)
    expect(ids.has('1')).toBe(true)
    expect(ids.has('4')).toBe(true)
  })

  it('clearSelection empties the set', () => {
    useApprovalsStore.getState().selectAllInGroup(['1', '2'])
    useApprovalsStore.getState().clearSelection()
    expect(useApprovalsStore.getState().selectedIds.size).toBe(0)
  })
})

describe('batchApprove', () => {
  it('rejects when batch size exceeds MAX_BATCH_SIZE', async () => {
    const ids = Array.from({ length: 51 }, (_, i) => `id-${i}`)
    const result = await useApprovalsStore.getState().batchApprove(ids)
    expect(result.succeeded).toBe(0)
    expect(result.failed).toBe(51)
    expect(result.failedReasons[0]).toContain('Batch size exceeds maximum of 50')
  })

  it('approves all items and returns success count', async () => {
    const items = [makeApproval('1'), makeApproval('2')]
    useApprovalsStore.setState({
      approvals: items,
      selectedIds: new Set(['1', '2']),
    })

    server.use(
      http.post('/api/v1/approvals/:id/approve', ({ params }) =>
        HttpResponse.json(
          apiSuccess(makeApproval(String(params.id), { status: 'approved' })),
        ),
      ),
    )

    const result = await useApprovalsStore
      .getState()
      .batchApprove(['1', '2'], 'Approved')

    expect(result).toEqual({ succeeded: 2, failed: 0, failedReasons: [] })
    expect(useApprovalsStore.getState().selectedIds.size).toBe(0)
  })

  it('rolls back failed items and returns mixed counts', async () => {
    const items = [makeApproval('1'), makeApproval('2')]
    useApprovalsStore.setState({
      approvals: items,
      selectedIds: new Set(['1', '2']),
    })

    server.use(
      http.post('/api/v1/approvals/:id/approve', ({ params }) => {
        if (params.id === '1') {
          return HttpResponse.json(
            apiSuccess(makeApproval('1', { status: 'approved' })),
          )
        }
        return HttpResponse.json(apiError('Server error'))
      }),
    )

    const result = await useApprovalsStore.getState().batchApprove(['1', '2'])

    expect(result).toEqual({
      succeeded: 1,
      failed: 1,
      failedReasons: ['Server error'],
    })
    expect(
      useApprovalsStore.getState().approvals.find((a) => a.id === '2')!.status,
    ).toBe('pending')
    const ids = useApprovalsStore.getState().selectedIds
    expect(ids.has('2')).toBe(true)
    expect(ids.has('1')).toBe(false)
  })
})

describe('batchReject', () => {
  it('rejects all items and returns success count', async () => {
    const items = [makeApproval('1'), makeApproval('2')]
    useApprovalsStore.setState({
      approvals: items,
      selectedIds: new Set(['1', '2']),
    })

    const capturedBodies: unknown[] = []
    server.use(
      http.post('/api/v1/approvals/:id/reject', async ({ params, request }) => {
        capturedBodies.push({ id: params.id, body: await request.json() })
        return HttpResponse.json(
          apiSuccess(makeApproval(String(params.id), { status: 'rejected' })),
        )
      }),
    )

    const result = await useApprovalsStore
      .getState()
      .batchReject(['1', '2'], 'Too risky')

    expect(result).toEqual({ succeeded: 2, failed: 0, failedReasons: [] })
    expect(capturedBodies).toHaveLength(2)
    for (const entry of capturedBodies) {
      expect((entry as { body: { reason: string } }).body.reason).toBe('Too risky')
    }
  })

  it('rolls back failed items and returns mixed counts', async () => {
    const items = [makeApproval('1'), makeApproval('2')]
    useApprovalsStore.setState({
      approvals: items,
      selectedIds: new Set(['1', '2']),
    })

    server.use(
      http.post('/api/v1/approvals/:id/reject', ({ params }) => {
        if (params.id === '1') {
          return HttpResponse.json(
            apiSuccess(makeApproval('1', { status: 'rejected' })),
          )
        }
        return HttpResponse.json(apiError('Server error'))
      }),
    )

    const result = await useApprovalsStore
      .getState()
      .batchReject(['1', '2'], 'Too risky')

    expect(result).toEqual({
      succeeded: 1,
      failed: 1,
      failedReasons: ['Server error'],
    })
    expect(
      useApprovalsStore.getState().approvals.find((a) => a.id === '2')!.status,
    ).toBe('pending')
  })
})
