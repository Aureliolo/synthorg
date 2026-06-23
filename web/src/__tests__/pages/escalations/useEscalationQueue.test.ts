import { act, renderHook, waitFor } from '@testing-library/react'
import { useEscalationQueue, ESCALATION_PAGE_SIZE } from '@/pages/escalations/useEscalationQueue'
import { useEscalationsStore } from '@/stores/escalations'
import type { EscalationResponse } from '@/api/types/escalations'

function makeResponse(id: string): EscalationResponse {
  return {
    escalation: {
      id,
      conflict: {
        id: `conflict-${id}`,
        type: 'resource',
        task_id: null,
        subject: `Conflict ${id}`,
        positions: [],
        detected_at: '2026-04-19T00:00:00Z',
        is_cross_department: false,
      },
      status: 'pending',
      created_at: '2026-04-19T00:00:00Z',
      expires_at: null,
      decided_at: null,
      decided_by: null,
      decision: null,
    },
    conflict_id: `conflict-${id}`,
    status: 'pending',
  }
}

function seedStore(count: number, hasMore: boolean): void {
  const escalations = Array.from({ length: count }, (_, i) => makeResponse(`esc-${i}`))
  act(() => {
    useEscalationsStore.setState({
      escalations,
      hasMore,
      loading: false,
      loadingMore: false,
      error: null,
      nextCursor: hasMore ? 'cursor-1' : null,
    })
  })
}

describe('useEscalationQueue paging', () => {
  it('windows the accumulated list to one page', async () => {
    const { result } = renderHook(() => useEscalationQueue())
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    seedStore(ESCALATION_PAGE_SIZE + 5, false)

    expect(result.current.visibleEscalations).toHaveLength(ESCALATION_PAGE_SIZE + 5)
    expect(result.current.page).toBe(1)
    expect(result.current.pagedEscalations).toHaveLength(ESCALATION_PAGE_SIZE)
  })

  it('advances to the next window via loadPage', async () => {
    const { result } = renderHook(() => useEscalationQueue())
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    seedStore(ESCALATION_PAGE_SIZE + 5, false)

    act(() => {
      result.current.loadPage(2)
    })

    expect(result.current.page).toBe(2)
    expect(result.current.pagedEscalations).toHaveLength(5)
  })

  it('does not advance past the end when nothing more can load', async () => {
    const { result } = renderHook(() => useEscalationQueue())
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    seedStore(ESCALATION_PAGE_SIZE + 5, false)

    act(() => {
      result.current.loadPage(2)
    })
    act(() => {
      result.current.loadPage(3)
    })

    // Page 2 is the last loaded window and hasMore is false, so the
    // attempt to reach page 3 is a no-op.
    expect(result.current.page).toBe(2)
  })
})
