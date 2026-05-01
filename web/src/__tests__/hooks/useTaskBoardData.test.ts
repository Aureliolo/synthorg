import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useTaskBoardData } from '@/hooks/useTaskBoardData'
import { useTasksStore } from '@/stores/tasks'
import { paginatedFor } from '@/mocks/handlers'
import type { listTasks } from '@/api/endpoints/tasks'
import { server } from '@/test-setup'

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn().mockReturnValue({
    connected: true,
    reconnectExhausted: false,
    setupError: null,
  }),
}))

vi.mock('@/hooks/usePolling', () => ({
  usePolling: vi.fn().mockReturnValue({
    active: false,
    error: null,
    start: vi.fn(),
    stop: vi.fn(),
  }),
}))

describe('useTaskBoardData', () => {
  beforeEach(() => {
    useTasksStore.setState({
      tasks: [],
      selectedTask: null,
      loading: false,
      loadingDetail: false,
      error: null,
    })
  })

  it('returns store state after initial fetch', async () => {
    const { result } = renderHook(() => useTaskBoardData())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.tasks).toEqual([])
    expect(result.current.total).toBe(0)
    expect(result.current.error).toBeNull()
  })

  it('returns WebSocket connection status', () => {
    const { result } = renderHook(() => useTaskBoardData())
    expect(result.current.wsConnected).toBe(true)
    expect(result.current.wsSetupError).toBeNull()
  })

  it('exposes store action references', () => {
    const { result } = renderHook(() => useTaskBoardData())
    expect(typeof result.current.fetchTask).toBe('function')
    expect(typeof result.current.createTask).toBe('function')
    expect(typeof result.current.updateTask).toBe('function')
    expect(typeof result.current.transitionTask).toBe('function')
    expect(typeof result.current.cancelTask).toBe('function')
    expect(typeof result.current.deleteTask).toBe('function')
    expect(typeof result.current.optimisticTransition).toBe('function')
  })

  it('triggers initial fetch on mount with the configured limit', async () => {
    const captured: { params: URLSearchParams | null } = { params: null }
    server.use(
      http.get('/api/v1/tasks', ({ request }) => {
        captured.params = new URL(request.url).searchParams
        return HttpResponse.json(
          paginatedFor<typeof listTasks>({
            data: [],
            limit: 200,
            nextCursor: null,
            hasMore: false,
            pagination: {
              limit: 200,
              next_cursor: null,
              has_more: false,
            },
          }),
        )
      }),
    )
    renderHook(() => useTaskBoardData())
    await waitFor(() => {
      expect(captured.params).not.toBeNull()
    })
    expect(captured.params?.get('limit')).toBe('200')
  })
})
