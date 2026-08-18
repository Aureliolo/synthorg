import type { OrderConstraint } from '@dagrejs/dagre'
import type { Edge, Node } from '@xyflow/react'

/** Members with no parent inside `memberIds`, keeping the given order. */
function rootsWithin(memberIds: readonly string[], edges: readonly Edge[]): string[] {
  const members = new Set(memberIds)
  const parented = new Set<string>()
  for (const edge of edges) {
    if (members.has(edge.source) && members.has(edge.target)) parented.add(edge.target)
  }
  return memberIds.filter((id) => !parented.has(id))
}

/**
 * Same-rank sibling sets within one unit, each in the given order.
 *
 * Every edge a unit is laid out from carries dagre's default rank distance of
 * one, so two members reached from the same parent land on the same rank, and
 * members with no parent in the unit are its first rank. That is the whole
 * basis for ordering without a second layout pass.
 *
 * Sibling sets are keyed by parent through a map rather than a joined string,
 * so no operator-authored name can make two different parents share a bucket.
 */
function bucketByParent(
  members: ReadonlySet<string>,
  edges: readonly Edge[],
): Map<string, string[]> {
  const byParent = new Map<string, string[]>()
  for (const edge of edges) {
    if (!members.has(edge.source) || !members.has(edge.target)) continue
    const bucket = byParent.get(edge.source) ?? []
    if (!bucket.includes(edge.target)) bucket.push(edge.target)
    byParent.set(edge.source, bucket)
  }
  return byParent
}

function sameRankSets(memberIds: readonly string[], edges: readonly Edge[]): string[][] {
  const order = new Map(memberIds.map((id, index) => [id, index]))
  const sets = [rootsWithin(memberIds, edges), ...bucketByParent(new Set(memberIds), edges).values()]
  for (const set of sets) set.sort((a, b) => order.get(a)! - order.get(b)!)
  return sets
}

/** What one unit's rank structure tells the layout, from a single traversal. */
export interface RankPlan {
  /** Consecutive `{left, right}` pairs over each rank, in the operator's order. */
  readonly constraints: OrderConstraint[]
}

/**
 * Read a unit's rank structure: how to pin its left-to-right order.
 *
 * `members` must already be in the operator's order. Emission order IS that
 * order: `build-org-tree` walks `config.departments`, and `groupAgentsByDept`
 * keeps each department's slice of the flat `config.agents` array intact. Both
 * arrays are what the `reorder-departments` / `reorder-agents` endpoints
 * persist, so pinning the chart to emission order makes it agree with the Org
 * Edit page instead of with whatever left-to-right slots dagre's barycentre
 * pass happened to pick.
 *
 * Only same-rank sets are chained. dagre resolves constraints against one
 * constraint graph shared by every layer of a sweep, which also accumulates
 * the edges its own subgraph pass writes back into it, so a pair whose
 * endpoints sit on different ranks is not inert: chaining every member of a
 * unit in one run reversed sibling pairs that the per-rank chains get right.
 */
export function planRanks(members: readonly Node[], edges: readonly Edge[]): RankPlan {
  const memberIds = members.map((member) => member.id)
  const constraints: OrderConstraint[] = []
  for (const set of sameRankSets(memberIds, edges)) {
    for (let index = 1; index < set.length; index++) {
      constraints.push({ left: set[index - 1]!, right: set[index]! })
    }
  }
  return { constraints }
}

/**
 * Map every leaf to the department that ultimately contains it.
 *
 * A leaf can sit inside a team box, which sits inside the department, so the
 * walk climbs until it reaches a department rather than reading `parentId`
 * once. The `seen` set bounds it: `parentId` comes from server data, and a
 * cycle in it would otherwise spin with the canvas already mounted.
 */
export function resolveDepartmentOf(
  allNodes: readonly Node[],
  departmentIds: ReadonlySet<string>,
  leaves: readonly Node[],
): Map<string, string> {
  const byId = new Map(allNodes.map((node) => [node.id, node]))
  const departmentOf = new Map<string, string>()
  for (const leaf of leaves) {
    let ancestor = leaf.parentId
    const seen = new Set<string>()
    while (ancestor !== undefined && !departmentIds.has(ancestor) && !seen.has(ancestor)) {
      seen.add(ancestor)
      ancestor = byId.get(ancestor)?.parentId
    }
    if (ancestor !== undefined && departmentIds.has(ancestor)) {
      departmentOf.set(leaf.id, ancestor)
    }
  }
  return departmentOf
}

/**
 * Edges rewritten so both endpoints are ids the given frame contains, with
 * anything inside a sub-unit replaced by the box that swallows it.
 *
 * Deduplication goes through a nested map rather than a joined key, so two
 * different pairs cannot collide on a name containing the separator.
 */
export function liftEdges(
  edges: readonly Edge[],
  liftOf: ReadonlyMap<string, string>,
): Edge[] {
  const seen = new Map<string, Set<string>>()
  const claim = (source: string, target: string): boolean => {
    const targets = seen.get(source) ?? new Set<string>()
    if (targets.has(target)) return false
    targets.add(target)
    seen.set(source, targets)
    return true
  }
  const lifted: Edge[] = []
  for (const edge of edges) {
    const source = liftOf.get(edge.source) ?? edge.source
    const target = liftOf.get(edge.target) ?? edge.target
    if (source === target || !claim(source, target)) continue
    lifted.push(
      source === edge.source && target === edge.target
        ? edge
        : { ...edge, id: `lift:${edge.id}`, source, target },
    )
  }
  return lifted
}
