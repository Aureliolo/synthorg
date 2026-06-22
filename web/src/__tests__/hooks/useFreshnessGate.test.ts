import { act, renderHook } from '@testing-library/react'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { FRESHNESS_WINDOW_MS } from '@/utils/ws-constants'

describe('useFreshnessGate', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('is stale before any WS update', () => {
    const { result } = renderHook(() => useFreshnessGate())
    expect(result.current.skipIfFresh()).toBe(false)
  })

  it('reports fresh immediately after markFresh', () => {
    const { result } = renderHook(() => useFreshnessGate())
    act(() => result.current.markFresh())
    expect(result.current.skipIfFresh()).toBe(true)
  })

  it('goes stale once the freshness window elapses', () => {
    const { result } = renderHook(() => useFreshnessGate())
    act(() => result.current.markFresh())

    act(() => {
      vi.advanceTimersByTime(FRESHNESS_WINDOW_MS - 1)
    })
    expect(result.current.skipIfFresh()).toBe(true)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.skipIfFresh()).toBe(false)
  })

  it('keeps stable identities across re-renders', () => {
    const { result, rerender } = renderHook(() => useFreshnessGate())
    const first = result.current
    rerender()
    expect(result.current.skipIfFresh).toBe(first.skipIfFresh)
    expect(result.current.markFresh).toBe(first.markFresh)
  })
})
