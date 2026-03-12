import { ref } from 'vue'
import { getErrorMessage } from '@/utils/errors'

/**
 * Perform an optimistic UI update with rollback on failure.
 *
 * @param applyOptimistic - Function to apply the optimistic state, returns rollback function.
 * @param serverAction - The actual server request.
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
    const rollback = applyOptimistic()

    try {
      const result = await serverAction()
      return result
    } catch (err) {
      rollback()
      error.value = getErrorMessage(err)
      return null
    } finally {
      pending.value = false
    }
  }

  return { pending, error, execute }
}
