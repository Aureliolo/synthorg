import { getTask } from '@/api/endpoints/tasks'
import type { Plan } from '@/api/types/plans'

/** Cap on concurrent parent-task lookups so a large inbox never floods the API. */
const RESOLVE_CONCURRENCY = 6

/**
 * Resolve each plan's human headline from its parent objective task. A plan
 * carries only ids, so the review inbox would otherwise show a bare UUID; this
 * batches the lookups (bounded concurrency) and tolerates a missing or orphaned
 * parent task by simply leaving that plan unresolved (the row falls back to the
 * objective id). Never throws: an unreachable task is swallowed per-plan.
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
      const task = await getTask(plan.parent_task_id).catch(() => null)
      if (task !== null) titles[plan.id] = task.title
    }
  }

  const workers = Array.from(
    { length: Math.min(RESOLVE_CONCURRENCY, plans.length) },
    () => worker(),
  )
  await Promise.all(workers)
  return titles
}
