/**
 * The plan's containment tree, derived from `parent_id` and nothing else.
 *
 * A recursive plan keeps almost all of its work below the top: an item with
 * children is not work to do, it is the assembly of the work below it. The
 * items nothing contains are the plan's workstreams, which is what makes a
 * hundred-item plan read as five tracks rather than a hundred rows.
 *
 * The mirror of `synthorg.core.plan_tree` on the backend, and derived the same
 * way for the same reason: "this item was split" cannot drift from "this item
 * has children" when nobody declares the first.
 */

import type { PlanItem } from '@/api/types/plans'

export interface PlanTree {
  /** Every item, keyed by its own id. */
  readonly byId: ReadonlyMap<string, PlanItem>
  /** Each container's id mapped to its children, in plan order. */
  readonly byParent: ReadonlyMap<string, readonly PlanItem[]>
  /** The items nothing contains, in plan order. */
  readonly workstreams: readonly PlanItem[]
}

/** Index a plan's items into its containment tree. */
export function buildPlanTree(items: readonly PlanItem[]): PlanTree {
  const byId = new Map(items.map((item) => [item.id, item]))
  const byParent = new Map<string, PlanItem[]>()
  for (const item of items) {
    // A parent naming nothing this plan holds leaves the child reading as a
    // workstream, which is the same answer the backend's tree view gives.
    if (item.parent_id === null || !byId.has(item.parent_id)) continue
    const bucket = byParent.get(item.parent_id) ?? []
    bucket.push(item)
    byParent.set(item.parent_id, bucket)
  }
  const workstreams = items.filter(
    (item) => item.parent_id === null || !byId.has(item.parent_id),
  )
  return { byId, byParent, workstreams }
}

/** What was split out of an item, empty for a leaf. */
export function childrenOf(tree: PlanTree, itemId: string): readonly PlanItem[] {
  return tree.byParent.get(itemId) ?? []
}

/** Whether an item is the assembly of the work below it rather than work. */
export function isContainer(tree: PlanTree, itemId: string): boolean {
  return childrenOf(tree, itemId).length > 0
}

export interface PlacedItem {
  readonly item: PlanItem
  /** How many levels sit above it: a workstream is 0. */
  readonly depth: number
  /** How many items it was split into, `0` when it is work rather than an assembly. */
  readonly childCount: number
  /**
   * Its position in the tree, such as `2.3`. A reviewer says which subtree a
   * comment is about with this rather than by quoting an id.
   */
  readonly label: string
}

/**
 * Every item, each workstream immediately followed by its whole subtree.
 *
 * The reading order for the review surface: a container, then what it
 * assembles, indented under it. Depth and label ride along so the caller
 * indents and numbers without walking back up the tree per row.
 */
export function placedByTree(tree: PlanTree): readonly PlacedItem[] {
  const placed: PlacedItem[] = []
  // Each item is placed at most once. The backend refuses a containment cycle,
  // so this cannot happen on a fetched plan; it is here because the cost of
  // being wrong about that is an unbounded recursion in the browser rather
  // than a wrong row, and every other reader of this module is downstream.
  const seen = new Set<string>()
  const walk = (item: PlanItem, depth: number, label: string): void => {
    if (seen.has(item.id)) return
    seen.add(item.id)
    const children = childrenOf(tree, item.id)
    placed.push({ item, depth, childCount: children.length, label })
    children.forEach((child, index) => walk(child, depth + 1, `${label}.${index + 1}`))
  }
  tree.workstreams.forEach((workstream, index) => walk(workstream, 0, `${index + 1}`))
  return placed
}

/** The workstream an item belongs to, itself when it is one. */
export function workstreamOf(tree: PlanTree, itemId: string): PlanItem | undefined {
  let current = tree.byId.get(itemId)
  const seen = new Set<string>()
  while (current !== undefined && current.parent_id !== null) {
    if (seen.has(current.id)) break
    seen.add(current.id)
    const parent = tree.byId.get(current.parent_id)
    if (parent === undefined) break
    current = parent
  }
  return current
}

/**
 * What each item actually waits on before it can start.
 *
 * A container waits on its own children as well as on whatever it declared,
 * because it assembles them. That edge is derived here and only here: it is
 * never written into `dependencies`, which stays the sole record of the order
 * the plan DECLARED, and this is the sole record of the order it RUNS in.
 */
export function dispatchDependencies(
  items: readonly PlanItem[],
): ReadonlyMap<string, readonly string[]> {
  const tree = buildPlanTree(items)
  return new Map(
    items.map((item) => [
      item.id,
      [...item.dependencies, ...childrenOf(tree, item.id).map((child) => child.id)],
    ]),
  )
}
