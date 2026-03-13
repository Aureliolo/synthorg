import { describe, it, expect, vi } from 'vitest'
import { useOptimisticUpdate } from '@/composables/useOptimisticUpdate'

vi.mock('@/utils/errors', () => ({
  getErrorMessage: (err: unknown) => (err instanceof Error ? err.message : 'Unknown error'),
}))

describe('useOptimisticUpdate', () => {
  it('returns pending, error, and execute', () => {
    const { pending, error, execute } = useOptimisticUpdate()
    expect(pending.value).toBe(false)
    expect(error.value).toBeNull()
    expect(typeof execute).toBe('function')
  })

  it('sets pending during execution and clears after', async () => {
    const { pending, execute } = useOptimisticUpdate()
    let pendingDuringAction = false

    await execute(
      () => () => {},
      () => {
        pendingDuringAction = pending.value
        return Promise.resolve('done')
      },
    )

    expect(pendingDuringAction).toBe(true)
    expect(pending.value).toBe(false)
  })

  it('returns server action result on success', async () => {
    const { execute } = useOptimisticUpdate()

    const result = await execute(
      () => () => {},
      () => Promise.resolve({ id: '123' }),
    )

    expect(result).toEqual({ id: '123' })
  })

  it('rolls back and returns null on server action failure', async () => {
    const { error, execute } = useOptimisticUpdate()
    let state = 'original'

    const result = await execute(
      () => {
        state = 'optimistic'
        return () => {
          state = 'original'
        }
      },
      () => Promise.reject(new Error('Server error')),
    )

    expect(result).toBeNull()
    expect(state).toBe('original')
    expect(error.value).toBe('Server error')
  })

  it('handles rollback failure gracefully', async () => {
    const { error, execute } = useOptimisticUpdate()
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    try {
      await execute(
        () => () => {
          throw new Error('Rollback boom')
        },
        () => Promise.reject(new Error('Server error')),
      )

      expect(error.value).toBe('Server error')
      // Rollback errors are logged via getErrorMessage (string), not raw Error
      expect(consoleSpy).toHaveBeenCalledWith('Rollback failed:', 'Rollback boom')
    } finally {
      consoleSpy.mockRestore()
    }
  })

  it('clears error on next execution', async () => {
    const { error, execute } = useOptimisticUpdate()

    await execute(
      () => () => {},
      () => Promise.reject(new Error('fail')),
    )
    expect(error.value).toBe('fail')

    await execute(
      () => () => {},
      () => Promise.resolve('ok'),
    )
    expect(error.value).toBeNull()
  })
})
