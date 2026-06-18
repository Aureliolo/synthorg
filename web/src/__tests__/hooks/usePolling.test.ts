import { renderHook, act } from '@testing-library/react'
import { usePolling } from '@/hooks/usePolling'

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('logs error and does not start for invalid interval', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 50))

    act(() => { result.current.start() })

    expect(consoleSpy).toHaveBeenCalledWith('[usePolling]', expect.stringContaining('intervalMs must be a finite number'))
    expect(result.current.active).toBe(false)
    expect(fn).not.toHaveBeenCalled()
    consoleSpy.mockRestore()
  })

  it('starts inactive', () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))
    expect(result.current.active).toBe(false)
    expect(fn).not.toHaveBeenCalled()
  })

  it('calls fn immediately on start', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0) // flush immediate call
    })

    expect(fn).toHaveBeenCalled()
    expect(result.current.active).toBe(true)
  })

  it('calls fn again after interval', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0) // immediate call
    })
    expect(fn).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000) // first interval
    })
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('stops polling on stop()', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })

    act(() => {
      result.current.stop()
    })
    expect(result.current.active).toBe(false)

    const callCount = fn.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fn).toHaveBeenCalledTimes(callCount) // no new calls
  })

  it('cleans up on unmount', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result, unmount } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })

    unmount()
    const callCount = fn.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fn).toHaveBeenCalledTimes(callCount)
  })

  it('exposes error state when fn throws', async () => {
    const fn = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('poll failed'))
      .mockResolvedValue(undefined)

    const { result } = renderHook(() => usePolling(fn, 1000))

    // Start -- first call succeeds
    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.error).toBeNull()

    // Second call fails
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(result.current.error).toContain('poll failed')

    // Third call succeeds -- error should clear
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(result.current.error).toBeNull()
  })

  it('does not overlap calls when fn is slow', async () => {
    let concurrentCalls = 0
    let maxConcurrent = 0

    // Gate each fn() on an explicit deferred the test resolves, so the
    // "slow call" is controlled directly rather than via a timer sleep.
    // With no-overlap only one fn is in flight at a time, so a single
    // pending resolver is sufficient.
    let releaseInFlight: () => void = () => {}
    const fn = vi.fn(async () => {
      concurrentCalls++
      maxConcurrent = Math.max(maxConcurrent, concurrentCalls)
      await new Promise<void>((resolve) => {
        releaseInFlight = resolve
      })
      concurrentCalls--
    })

    const { result } = renderHook(() => usePolling(fn, 500))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(5000)
    })

    // The hook schedules the next tick only after the previous fn settles,
    // so a still-in-flight call prevents overlap.
    expect(maxConcurrent).toBeLessThanOrEqual(1)

    // Stop polling and release the in-flight fn() so its promise settles
    // before teardown. Without this the pending await outlives the test
    // boundary and the active-handle gate fails the test.
    await act(async () => {
      result.current.stop()
      releaseInFlight()
      await vi.advanceTimersByTimeAsync(5000)
    })
  })

  it('ignores duplicate start calls', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      result.current.start() // duplicate
      await vi.advanceTimersByTimeAsync(0)
    })

    // Only called once from the first start
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('does not spawn duplicate loop after stop/start while inflight', async () => {
    let resolveFirst: () => void
    const firstCall = new Promise<void>((r) => { resolveFirst = r })
    const fn = vi.fn().mockImplementationOnce(() => firstCall).mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    // Start first run
    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fn).toHaveBeenCalledTimes(1)

    // Stop + start while first call is still pending
    act(() => {
      result.current.stop()
      result.current.start()
    })

    // Resolve the first (now-stale) call
    await act(async () => {
      resolveFirst!()
      await vi.advanceTimersByTimeAsync(0)
    })

    // Second run should have fired from the new start, not the stale completion
    expect(fn).toHaveBeenCalledTimes(2)

    // Advance one interval -- should still only have one active loop
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fn).toHaveBeenCalledTimes(3)
  })

  it('sets error state on first-tick failure', async () => {
    const fn = vi.fn().mockRejectedValueOnce(new Error('initial poll failed'))
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      // The first tick runs inside an async microtask; flush enough
      // time for both the microtask queue and the setError commit to
      // settle so we read post-render state.
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.error).toContain('initial poll failed')
  })

  it('exposes isRefetching during in-flight fn and clears it after', async () => {
    let resolveFn: () => void
    const pending = new Promise<void>((resolve) => { resolveFn = resolve })
    const fn = vi.fn().mockImplementationOnce(() => pending).mockResolvedValue(undefined)
    const { result } = renderHook(() => usePolling(fn, 1000))

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isRefetching).toBe(true)

    await act(async () => {
      resolveFn!()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isRefetching).toBe(false)

    // Drain the next scheduled tick so the active-handle gate sees no leak.
    act(() => {
      result.current.stop()
    })
  })

  it('skips ticks when skipIfFresh returns true', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    let fresh = true
    const { result } = renderHook(() =>
      usePolling(fn, 1000, { skipIfFresh: () => fresh }),
    )

    await act(async () => {
      result.current.start()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fn).toHaveBeenCalledTimes(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fn).toHaveBeenCalledTimes(0)

    fresh = false
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fn).toHaveBeenCalledTimes(1)

    act(() => {
      result.current.stop()
    })
  })

  it('skips ticks while document.hidden is true', async () => {
    const fn = vi.fn().mockResolvedValue(undefined)
    const hiddenDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden')
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => true,
    })
    try {
      const { result } = renderHook(() => usePolling(fn, 1000))
      await act(async () => {
        result.current.start()
        await vi.advanceTimersByTimeAsync(0)
      })
      // Hidden tab: the immediate run is also gated; no calls.
      expect(fn).toHaveBeenCalledTimes(0)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(fn).toHaveBeenCalledTimes(0)

      act(() => {
        result.current.stop()
      })
    } finally {
      if (hiddenDescriptor) {
        Object.defineProperty(document, 'hidden', hiddenDescriptor)
      } else {
        Reflect.deleteProperty(document, 'hidden')
      }
    }
  })
})
