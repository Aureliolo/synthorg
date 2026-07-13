/**
 * Version diff across plan revisions: classify how the current items differ from
 * a prior version snapshot (added / removed / modified, and which fields), so a
 * reviewer can see how a rework addressed the panel's concerns. Pure functions,
 * split from `plans.ts` to keep each module within its size budget.
 */

import type { PlanItem, PlanVersionSnapshot } from '@/api/types/plans'

export type ItemChange = 'added' | 'removed' | 'modified'

export interface ItemDiff {
  readonly id: string
  readonly title: string
  readonly change: ItemChange
  /** Human labels of the fields that changed (modified items only). */
  readonly changedFields: readonly string[]
}

export interface PlanDiff {
  readonly fromVersion: number
  readonly toVersion: number
  readonly added: readonly ItemDiff[]
  readonly removed: readonly ItemDiff[]
  readonly modified: readonly ItemDiff[]
  /** Items present in both versions with no change. */
  readonly unchanged: number
}

// Fields compared for a "modified" item, with the label shown when they differ.
const DIFF_FIELDS: ReadonlyArray<readonly [keyof PlanItem, string]> = [
  ['title', 'title'],
  ['description', 'description'],
  ['owner', 'owner'],
  ['estimated_complexity', 'complexity'],
  ['stakes', 'stakes'],
  ['dependencies', 'dependencies'],
  ['acceptance_criteria', 'acceptance criteria'],
  ['expected_artifacts', 'artifacts'],
  ['satisfies', 'coverage'],
  ['chosen_option_id', 'chosen option'],
]

function fieldDiffers(a: PlanItem, b: PlanItem, field: keyof PlanItem): boolean {
  const av = a[field]
  const bv = b[field]
  if (Array.isArray(av) && Array.isArray(bv)) {
    return av.length !== bv.length || av.some((v, i) => v !== bv[i])
  }
  return av !== bv
}

function changedFields(prev: PlanItem, current: PlanItem): string[] {
  return DIFF_FIELDS.filter(([field]) => fieldDiffers(prev, current, field)).map(
    ([, label]) => label,
  )
}

/**
 * Diff the current plan items against a prior version snapshot: which items were
 * added, removed, or modified (and which of their fields changed). Matching is
 * by item id.
 */
export function derivePlanDiff(
  previous: PlanVersionSnapshot,
  current: { readonly items: readonly PlanItem[]; readonly version: number },
): PlanDiff {
  const prevById = new Map(previous.items.map((item) => [item.id, item]))
  const currentIds = new Set(current.items.map((item) => item.id))
  const added: ItemDiff[] = []
  const modified: ItemDiff[] = []
  let unchanged = 0
  for (const item of current.items) {
    const before = prevById.get(item.id)
    if (before === undefined) {
      added.push({ id: item.id, title: item.title, change: 'added', changedFields: [] })
      continue
    }
    const fields = changedFields(before, item)
    if (fields.length > 0) {
      modified.push({
        id: item.id,
        title: item.title,
        change: 'modified',
        changedFields: fields,
      })
    } else {
      unchanged += 1
    }
  }
  const removed: ItemDiff[] = previous.items
    .filter((item) => !currentIds.has(item.id))
    .map((item) => ({
      id: item.id,
      title: item.title,
      change: 'removed' as const,
      changedFields: [],
    }))
  return {
    fromVersion: previous.version,
    toVersion: current.version,
    added,
    removed,
    modified,
    unchanged,
  }
}
