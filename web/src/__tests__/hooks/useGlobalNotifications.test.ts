import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useGlobalNotifications } from '@/hooks/useGlobalNotifications'
import { useAgentsStore } from '@/stores/agents'
import { useToastStore } from '@/stores/toast'
import { useWebSocketStore } from '@/stores/websocket'
import type { WsEvent } from '@/api/types/websocket'

// Mock the useWebSocket hook so we can control connection state and capture
// the bindings that useGlobalNotifications subscribes with.
const mockUseWebSocket = vi.fn<(...args: unknown[]) => unknown>()
vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: (...args: unknown[]) => mockUseWebSocket(...args),
}))

describe('useGlobalNotifications', () => {
  beforeEach(() => {
    mockUseWebSocket.mockReset()
    mockUseWebSocket.mockReturnValue({
      connected: true,
      reconnectExhausted: false,
      setupError: null,
    })
    useAgentsStore.setState({ runtimeStatuses: {} })
    // Global afterEach in test-setup.tsx already handles
    // `dismissAll()` + `cancelPendingPersist()`; do NOT duplicate
    // here -- double-teardown measurably raises the async-leak count
    // on CI. Keep a single pre-test reset so a state-dirty prior test
    // cannot seed this one.
    useToastStore.getState().dismissAll()
  })

  afterEach(() => {
    // Restore any vi.spyOn spies created during the test.  If a test
    // fails before its inline `.mockRestore()` call runs, the spy would
    // leak into subsequent tests; this unconditional restore keeps the
    // suite isolated regardless of where a failure occurs.
    vi.restoreAllMocks()
  })

  it('subscribes to the agents channel', () => {
    renderHook(() => useGlobalNotifications())

    expect(mockUseWebSocket).toHaveBeenCalledTimes(1)
    const [options] = mockUseWebSocket.mock.calls[0]!
    const bindings = (options as { bindings: Array<{ channel: string }> }).bindings
    // Shape assertion (not count) so adding channels does not break the test.
    expect(bindings.some((b) => b.channel === 'agents')).toBe(true)
  })

  it('dispatches WS events to the agents store', () => {
    renderHook(() => useGlobalNotifications())

    const [options] = mockUseWebSocket.mock.calls[0]!
    const { bindings } = options as {
      bindings: Array<{ channel: string; handler: (event: WsEvent) => void }>
    }
    // Resolve by channel name rather than index so adding unrelated
    // subscriptions upstream cannot silently break this test.
    const agentsBinding = bindings.find((b) => b.channel === 'agents')
    expect(agentsBinding).toBeDefined()

    agentsBinding!.handler({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-04-05T10:00:00Z',
      payload: { agent_id: 'agent-1', to_status: 'active' },
    })

    expect(useAgentsStore.getState().runtimeStatuses['agent-1']).toBe('active')
  })

  it('delegates personality.trimmed events to the agents store', () => {
    // This hook is only responsible for wiring the `agents` binding.  The
    // toast contents (title, variant, description) are built inside
    // `useAgentsStore.updateFromWsEvent` and are covered by the agents
    // store test suite.  Spying on the store method here keeps this test
    // focused on the delegation contract so unrelated copy changes in the
    // store do not cascade into this file.
    const spy = vi.spyOn(useAgentsStore.getState(), 'updateFromWsEvent')
    renderHook(() => useGlobalNotifications())

    const [options] = mockUseWebSocket.mock.calls[0]!
    const { bindings } = options as {
      bindings: Array<{ channel: string; handler: (event: WsEvent) => void }>
    }
    const agentsBinding = bindings.find((b) => b.channel === 'agents')
    expect(agentsBinding).toBeDefined()

    const event: WsEvent = {
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-04-05T10:00:00Z',
      payload: {
        agent_id: 'agent-1',
        agent_name: 'Alice',
        task_id: 'task-1',
        before_tokens: 600,
        after_tokens: 120,
        max_tokens: 200,
        trim_tier: 2,
        budget_met: true,
      },
    }
    agentsBinding!.handler(event)

    expect(spy).toHaveBeenCalledWith(event)
    spy.mockRestore()
  })

  it.each([
    {
      name: 'warning toast when WebSocket setup fails',
      wsState: {
        connected: false,
        reconnectExhausted: false,
        setupError: 'WebSocket connection failed.',
      },
      expectedVariant: 'warning' as const,
      expectedTitle: 'Live notifications unavailable',
    },
    {
      name: 'error toast when reconnect is exhausted',
      wsState: {
        connected: false,
        reconnectExhausted: true,
        setupError: null,
      },
      expectedVariant: 'error' as const,
      expectedTitle: 'Live notifications disconnected',
    },
  ])('renders a $name', ({ wsState, expectedVariant, expectedTitle }) => {
    mockUseWebSocket.mockReturnValue(wsState)

    renderHook(() => useGlobalNotifications())

    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe(expectedVariant)
    expect(toasts[0]!.title).toBe(expectedTitle)
  })

  it('does not emit a toast when everything is healthy', () => {
    renderHook(() => useGlobalNotifications())
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('deduplicates identical setupError values across re-renders', () => {
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: false,
      setupError: 'WebSocket connection failed.',
    })

    const { rerender } = renderHook(() => useGlobalNotifications())
    rerender()
    rerender()

    // lastSetupErrorRef dedupes identical errors across re-renders -- only a
    // single toast should have been emitted.
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('resets dedupe refs when the WS successfully reconnects', () => {
    // 1. WS down with setup error -> one warning toast.
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: false,
      setupError: 'First failure',
    })
    const { rerender } = renderHook(() => useGlobalNotifications())
    expect(useToastStore.getState().toasts).toHaveLength(1)
    useToastStore.getState().dismissAll()

    // 2. Reconnect succeeds -> no toast, but refs should reset so a future
    // failure fires a fresh warning instead of being silently deduped.
    mockUseWebSocket.mockReturnValue({
      connected: true,
      reconnectExhausted: false,
      setupError: null,
    })
    rerender()
    expect(useToastStore.getState().toasts).toHaveLength(0)

    // 3. Second failure with an IDENTICAL string to the first one.  If refs
    // were not reset, dedupe would suppress the toast.
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: false,
      setupError: 'First failure',
    })
    rerender()
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('resets the reconnect-exhausted ref when the WS recovers', () => {
    // Mirror of the setupError dedupe sequence above, but for the separate
    // reconnectExhaustedRef.  A flapping connection should emit a fresh
    // error toast on each exhaustion, not silently stay quiet after the
    // first one.
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: true,
      setupError: null,
    })
    const { rerender } = renderHook(() => useGlobalNotifications())
    expect(useToastStore.getState().toasts).toHaveLength(1)
    useToastStore.getState().dismissAll()

    // Reconnect succeeds -- refs should reset.
    mockUseWebSocket.mockReturnValue({
      connected: true,
      reconnectExhausted: false,
      setupError: null,
    })
    rerender()
    expect(useToastStore.getState().toasts).toHaveLength(0)

    // Second exhaustion -- if the ref was not reset the toast would be
    // suppressed.
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: true,
      setupError: null,
    })
    rerender()
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('wires a Retry action on the reconnect-exhausted toast that calls useWebSocketStore.retry()', () => {
    mockUseWebSocket.mockReturnValue({
      connected: false,
      reconnectExhausted: true,
      setupError: null,
    })
    const retrySpy = vi
      .spyOn(useWebSocketStore.getState(), 'retry')
      .mockResolvedValue(undefined)

    renderHook(() => useGlobalNotifications())

    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    const action = toasts[0]!.action
    expect(action).toBeDefined()
    expect(action!.label).toBe('Retry')

    action!.onClick()
    expect(retrySpy).toHaveBeenCalledTimes(1)
  })

  it('unmount is a no-op that leaves the store untouched', () => {
    // Teardown of the underlying WebSocket subscription is owned by
    // useWebSocket (which is mocked here) -- this hook has no side-effect
    // cleanup of its own beyond effect dep-array resets. Assert the baseline
    // "nothing left in the store after unmount" invariant; deeper cleanup
    // verification lives in useWebSocket's own test suite.
    mockUseWebSocket.mockReturnValue({
      connected: true,
      reconnectExhausted: false,
      setupError: null,
    })
    const { unmount } = renderHook(() => useGlobalNotifications())
    expect(() => unmount()).not.toThrow()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
