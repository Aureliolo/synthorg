import type { OrderConstraint } from '@dagrejs/dagre'
import type { Edge, Node } from '@xyflow/react'
import {
  type ClusterDirection,
  DEFAULT_NODE_WIDTH,
  DEPT_HORIZONTAL_WIDTH_BUDGET,
  dagreEdgeMinlen,
} from './layout-shared'

/**
 * Pick a unit's internal flow from what it would cost laid out top-to-bottom:
 * one rank of `widestRankSize` cards side by side.
 */
export function chooseClusterDirection(
  widestRankSize: number,
  nodeSep: number,
): ClusterDirection {
  const stackedWidth = widestRankSize * (DEFAULT_NODE_WIDTH + nodeSep) - nodeSep
  return stackedWidth > DEPT_HORIZONTAL_WIDTH_BUDGET ? 'LR' : 'TB'
}

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
 * Two members reached from the same parent over edges of equal minlen land on
 * the same rank, and members with no parent in the unit are its first rank.
 * That is the whole basis for ordering without a second layout pass: each unit
 * is laid out from a tree, so an edge of a given minlen always lands its
 * target exactly that far below its parent.
 *
 * Sibling sets are keyed through nested maps rather than a joined string, so
 * no operator-authored name can make two different parents share a bucket.
 */
function bucketByParentAndMinlen(
  members: ReadonlySet<string>,
  edges: readonly Edge[],
): Map<string, Map<number, string[]>> {
  const byParent = new Map<string, Map<number, string[]>>()
  for (const edge of edges) {
    if (!members.has(edge.source) || !members.has(edge.target)) continue
    const byMinlen = byParent.get(edge.source) ?? new Map<number, string[]>()
    const minlen = dagreEdgeMinlen(edge)
    const bucket = byMinlen.get(minlen) ?? []
    if (!bucket.includes(edge.target)) bucket.push(edge.target)
    byMinlen.set(minlen, bucket)
    byParent.set(edge.source, byMinlen)
  }
  return byParent
}

function sameRankSets(memberIds: readonly string[], edges: readonly Edge[]): string[][] {
  const order = new Map(memberIds.map((id, index) => [id, index]))
  const sets = [rootsWithin(memberIds, edges)]
  for (const byMinlen of bucketByParentAndMinlen(new Set(memberIds), edges).values()) {
    sets.push(...byMinlen.values())
  }
  for (const set of sets) set.sort((a, b) => order.get(a)! - order.get(b)!)
  return sets
}

/** What one unit's rank structure tells the layout, from a single traversal. */
export interface RankPlan {
  /** How many cards the widest rank would put side by side. */
  readonly widestRank: number
  /** Consecutive `{left, right}` pairs over each rank, in the operator's order. */
  readonly constraints: OrderConstraint[]
}

/**
 * Read a unit's rank structure: how wide it gets, and how to pin its order.
 *
 * `memberIds` must already be in the operator's order. Emission order IS that
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
export function planRanks(memberIds: readonly string[], edges: readonly Edge[]): RankPlan {
  const sets = sameRankSets(memberIds, edges)
  const constraints: OrderConstraint[] = []
  for (const set of sets) {
    for (let index = 1; index < set.length; index++) {
      constraints.push({ left: set[index - 1]!, right: set[index]! })
    }
  }
  return { widestRank: Math.max(...sets.map((set) => set.length)), constraints }
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
