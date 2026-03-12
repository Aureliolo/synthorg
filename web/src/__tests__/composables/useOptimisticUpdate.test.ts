import { describe, it, expect, vi } from 'vitest'

// Test the core optimistic update logic
describe('optimistic update pattern', () => {
  it('applies and rolls back on failure', async () => {
    let state = 'original'

    const applyOptimistic = () => {
      const previous = state
      state = 'optimistic'
      return () => {
        state = previous
      }
    }

    const serverAction = vi.fn().mockRejectedValue(new Error('Server error'))

    const rollback = applyOptimistic()
    expect(state).toBe('optimistic')

    try {
      await serverAction()
    } catch {
      rollback()
    }

    expect(state).toBe('original')
  })

  it('keeps optimistic state on success', async () => {
    let state = 'original'

    const applyOptimistic = () => {
      state = 'optimistic'
      return () => {
        state = 'original'
      }
    }

    const serverAction = vi.fn().mockResolvedValue({ success: true })

    applyOptimistic()
    expect(state).toBe('optimistic')

    await serverAction()
    expect(state).toBe('optimistic')
  })
})
