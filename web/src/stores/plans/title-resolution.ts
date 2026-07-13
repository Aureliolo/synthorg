import { getTask } from '@/api/endpoints/tasks'
import type { Plan } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plans')

/** Cap on concurrent parent-task lookups so a large inbox never floods the API. */
const RESOLVE_CONCURRENCY = 6

/** HTTP status for an absent parent task: an orphaned plan, not a real failure. */
const HTTP_NOT_FOUND = 404

/**
 * Resolve each plan's human headline from its parent objective task. A plan
 * carries only ids, so the review inbox would otherwise show a bare UUID; this
 * batches the lookups (bounded concurrency). A 404 means the parent task is
 * gone (an orphaned plan), so that plan is left unresolved and its row falls
 * back to the objective id; any other failure is surfaced at WARNING rather
 * than silently swallowed as a missing task. Never throws.
 */
export async function resolvePlanTitles(
  plans: readonly Plan[],
): Promise<Record<string, string>> {
  const titles: Record<string, string> = {}
  const queue = [...plans]

  async function worker(): Promise<void> {
    for (;;) {
      const plan = queue.shift()
      if (plan === undefined) return
      try {
        const task = await getTask(plan.parent_task_id)
        titles[plan.id] = task.title
      } catch (err) {
        if (!isAxiosError(err) || err.response?.status !== HTTP_NOT_FOUND) {
          log.warn('Parent task title lookup failed:', sanitizeForLog(err))
        }
      }
    }
  }

  const workers = Array.from(
    { length: Math.min(RESOLVE_CONCURRENCY, plans.length) },
    () => worker(),
  )
  await Promise.all(workers)
  return titles
}
