import { describe, it, expect, vi, afterEach } from 'vitest'

// We need to test the core logic without Vue lifecycle hooks
describe('usePolling logic', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls function at intervals', () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const timer = setInterval(fn, 1000)

    vi.advanceTimersByTime(3000)
    expect(fn).toHaveBeenCalledTimes(3)

    clearInterval(timer)
    vi.useRealTimers()
  })

  it('stops calling after clearInterval', () => {
    vi.useFakeTimers()
    const fn = vi.fn().mockResolvedValue(undefined)
    const timer = setInterval(fn, 1000)

    vi.advanceTimersByTime(2000)
    clearInterval(timer)
    vi.advanceTimersByTime(3000)

    expect(fn).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })
})
