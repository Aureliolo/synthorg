import { ref } from 'vue'
import { getErrorMessage } from '@/utils/errors'

/**
 * Perform an optimistic UI update with rollback on failure.
 *
 * Returns an `execute(applyOptimistic, serverAction)` function where
 * `applyOptimistic` applies the optimistic state and returns a rollback function,
 * and `serverAction` is the actual server request.
 */
export function useOptimisticUpdate() {
  const pending = ref(false)
  const error = ref<string | null>(null)

  async function execute<T>(
    applyOptimistic: () => () => void,
    serverAction: () => Promise<T>,
  ): Promise<T | null> {
    pending.value = true
    error.value = null
    let rollback: (() => void) | null = null

    try {
      rollback = applyOptimistic()
      const result = await serverAction()
      return result
    } catch (err) {
      try {
        rollback?.()
      } catch (rollbackErr) {
        console.error('Rollback failed:', rollbackErr)
      }
      error.value = getErrorMessage(err)
      console.error('Optimistic update failed:', err)
      return null
    } finally {
      pending.value = false
    }
  }

  return { pending, error, execute }
}
