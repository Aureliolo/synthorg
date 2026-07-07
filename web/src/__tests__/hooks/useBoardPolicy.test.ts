import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { useBoardPolicy } from '@/hooks/useBoardPolicy'
import { successFor } from '@/mocks/handlers/helpers'
import type { getBoard } from '@/api/endpoints/board'

describe('useBoardPolicy', () => {
  it('maps backend flow-limited columns onto frontend column ids', async () => {
    const { result } = renderHook(() => useBoardPolicy(true))
    await waitFor(() => expect(result.current.policy).not.toBeNull())
    const policy = result.current.policy
    expect(policy?.enforceWip).toBe(false)
    expect(policy?.wipByColumn.in_progress).toEqual({
      count: 2,
      limit: 5,
      overLimit: false,
    })
    // Backend 'review' column maps onto the frontend 'in_review' lane.
    expect(policy?.wipByColumn.in_review).toEqual({
      count: 1,
      limit: 3,
      overLimit: false,
    })
    // Unlimited columns carry no WIP badge.
    expect(policy?.wipByColumn.backlog).toBeUndefined()
  })

  it('surfaces the over-limit flag from the backend', async () => {
    server.use(
      http.get('/api/v1/board', () =>
        HttpResponse.json(
          successFor<typeof getBoard>({
            workflow_type: 'agile_kanban',
            enforce_wip: true,
            columns: [
              {
                column: 'in_progress',
                tasks: [],
                count: 6,
                limit: 5,
                over_limit: true,
              },
              { column: 'review', tasks: [], count: 0, limit: 3, over_limit: false },
            ],
          }),
        ),
      ),
    )
    const { result } = renderHook(() => useBoardPolicy(true))
    await waitFor(() => expect(result.current.policy).not.toBeNull())
    expect(result.current.policy?.enforceWip).toBe(true)
    expect(result.current.policy?.wipByColumn.in_progress?.overLimit).toBe(true)
  })

  it('does not fetch while disabled (list view)', async () => {
    let hits = 0
    server.use(
      http.get('/api/v1/board', () => {
        hits += 1
        return new HttpResponse(null, { status: 500 })
      }),
    )
    const { result } = renderHook(() => useBoardPolicy(false))
    await waitFor(() => expect(hits).toBe(0))
    expect(result.current.policy).toBeNull()
  })

  it('degrades to null when the board fetch fails', async () => {
    let hits = 0
    server.use(
      http.get('/api/v1/board', () => {
        hits += 1
        return new HttpResponse(null, { status: 503 })
      }),
    )
    const { result } = renderHook(() => useBoardPolicy(true))
    // Wait until the failing fetch has actually been attempted, then confirm
    // the hook swallowed it and kept policy null (board renders sans badges).
    await waitFor(() => expect(hits).toBeGreaterThan(0))
    expect(result.current.policy).toBeNull()
  })
})
