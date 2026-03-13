import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { ref } from 'vue'

vi.mock('vue', async () => {
  const actual = await vi.importActual<typeof import('vue')>('vue')
  return {
    ...actual,
    onUnmounted: vi.fn(),
  }
})

vi.mock('@/utils/errors', () => ({
  isAxiosError: (err: unknown) =>
    typeof err === 'object' && err !== null && 'isAxiosError' in err,
}))

import { useLoginLockout } from '@/composables/useLoginLockout'

function makeAxiosError(status: number) {
  return { isAxiosError: true, response: { status } }
}

describe('useLoginLockout', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts unlocked with no attempts', () => {
    const { locked } = useLoginLockout()
    expect(locked.value).toBe(false)
  })

  it('does not lock on network/5xx errors', () => {
    const { recordFailure, locked } = useLoginLockout()
    for (let i = 0; i < 10; i++) {
      recordFailure(new Error('Network error'))
    }
    expect(locked.value).toBe(false)
  })

  it('does not lock on 5xx errors', () => {
    const { recordFailure, locked } = useLoginLockout()
    for (let i = 0; i < 10; i++) {
      recordFailure(makeAxiosError(500))
    }
    expect(locked.value).toBe(false)
  })

  it('locks after LOGIN_MAX_ATTEMPTS credential failures', () => {
    const { recordFailure, locked } = useLoginLockout()
    for (let i = 0; i < 4; i++) {
      expect(recordFailure(makeAxiosError(401))).toBeNull()
    }
    const msg = recordFailure(makeAxiosError(401))
    expect(msg).toContain('Too many failed attempts')
    expect(locked.value).toBe(true)
  })

  it('checkAndClearLockout clears expired lockout', () => {
    const { recordFailure, locked, checkAndClearLockout } = useLoginLockout()
    for (let i = 0; i < 5; i++) {
      recordFailure(makeAxiosError(401))
    }
    expect(locked.value).toBe(true)

    vi.advanceTimersByTime(61_000)
    const stillLocked = checkAndClearLockout()
    expect(stillLocked).toBe(false)
  })

  it('reset clears attempts and lockout', () => {
    const { recordFailure, locked, reset } = useLoginLockout()
    for (let i = 0; i < 5; i++) {
      recordFailure(makeAxiosError(401))
    }
    expect(locked.value).toBe(true)
    reset()
    expect(locked.value).toBe(false)
  })

  it('counts only 4xx errors toward lockout', () => {
    const { recordFailure, locked } = useLoginLockout()
    recordFailure(makeAxiosError(401))
    recordFailure(makeAxiosError(403))
    recordFailure(new Error('network'))
    recordFailure(makeAxiosError(500))
    recordFailure(makeAxiosError(422))
    // 3 credential failures (401, 403, 422) — not yet at 5
    expect(locked.value).toBe(false)
  })
})
