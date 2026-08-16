import { act, renderHook } from '@testing-library/react'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import {
  FRESHNESS_WINDOW_MS,
  MAX_CONSECUTIVE_FRESH_SKIPS,
} from '@/utils/ws-constants'

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

  it('lets a poll through once the skip cap is reached, however fresh', () => {
    // A WS frame only ever adds or updates a row; the REST refetch is what
    // reconciles. An unbounded skip therefore lets a continuously-updated
    // store keep rows the server has deleted, with a page reload as the only
    // cure -- which is how a sidebar badge counted three plan reviews against
    // a page showing none.
    const { result } = renderHook(() => useFreshnessGate())

    for (let i = 0; i < MAX_CONSECUTIVE_FRESH_SKIPS; i += 1) {
      act(() => result.current.markFresh())
      expect(result.current.skipIfFresh()).toBe(true)
    }

    act(() => result.current.markFresh())
    expect(result.current.skipIfFresh()).toBe(false)
  })

  it('re-arms the skip budget after a poll runs', () => {
    const { result } = renderHook(() => useFreshnessGate())
    for (let i = 0; i < MAX_CONSECUTIVE_FRESH_SKIPS; i += 1) {
      act(() => result.current.markFresh())
      result.current.skipIfFresh()
    }
    act(() => result.current.markFresh())
    expect(result.current.skipIfFresh()).toBe(false)

    act(() => result.current.markFresh())
    expect(result.current.skipIfFresh()).toBe(true)
  })

  it('keeps stable identities across re-renders', () => {
    const { result, rerender } = renderHook(() => useFreshnessGate())
    const first = result.current
    rerender()
    expect(result.current.skipIfFresh).toBe(first.skipIfFresh)
    expect(result.current.markFresh).toBe(first.markFresh)
  })
})
