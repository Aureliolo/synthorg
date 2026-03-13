import { describe, it, expect, vi, afterEach } from 'vitest'

// Mock Vue's onUnmounted since we're not in a component context
vi.mock('vue', async () => {
  const actual = await vi.importActual<typeof import('vue')>('vue')
  return {
    ...actual,
    onUnmounted: vi.fn(),
  }
})

import { usePolling } from '@/composables/usePolling'

describe('usePolling', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('returns active, start, and stop', () => {
    const { active, start, stop } = usePolling(vi.fn().mockResolvedValue(undefined), 1000)
    expect(active.value).toBe(false)
    expect(typeof start).toBe('function')
    expect(typeof stop).toBe('function')
  })

  it('calls fn immediately on start then at intervals', async () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const { active, start } = usePolling(fn, 1000)

    start()
    expect(active.value).toBe(true)

    // fn is called immediately on start
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)

    // After 1 interval
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(2)

    // After 2 intervals
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(3)
  })

  it('stop clears interval and sets active to false', async () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const { active, start, stop } = usePolling(fn, 1000)

    start()
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)

    stop()
    expect(active.value).toBe(false)

    await vi.advanceTimersByTimeAsync(3000)
    expect(fn).toHaveBeenCalledTimes(1) // no more calls after stop
  })

  it('duplicate start() calls are no-ops', async () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const { start } = usePolling(fn, 1000)

    start()
    start() // should be no-op
    start() // should be no-op

    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1) // only one immediate call
  })

  it('swallows errors from fn and continues polling', async () => {
    vi.useFakeTimers()
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const fn = vi.fn()
      .mockRejectedValueOnce(new Error('fail'))
      .mockResolvedValue(undefined)
    const { start } = usePolling(fn, 1000)

    start()
    await vi.advanceTimersByTimeAsync(0) // first call — errors
    expect(consoleSpy).toHaveBeenCalledWith('Polling error:', expect.any(Error))

    await vi.advanceTimersByTimeAsync(1000) // second call — succeeds
    expect(fn).toHaveBeenCalledTimes(2)
    consoleSpy.mockRestore()
  })

  it('safeFn returns early when active is false', async () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const { start, stop } = usePolling(fn, 1000)

    start()
    await vi.advanceTimersByTimeAsync(0) // immediate call
    expect(fn).toHaveBeenCalledTimes(1)

    stop()
    // Even though the interval fires, safeFn checks active and returns early
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(1)
  })
})
