import type { OrderConstraint } from '@dagrejs/dagre'
import type { Edge, Node } from '@xyflow/react'
import {
  type ClusterDirection,
  DEFAULT_NODE_WIDTH,
  DEPT_HORIZONTAL_WIDTH_BUDGET,
  dagreEdgeMinlen,
} from './layout-shared'

/** One department laid out in isolation, in its own direction. */
export interface ClusterPlan {
  /** The department group node's id. */
  readonly id: string
  /** Leaf nodes laid out inside this department, in the operator's order. */
  readonly memberIds: readonly string[]
  readonly direction: ClusterDirection
}

/**
 * What the org data, rather than dagre's heuristics, decides about the
 * chart's arrangement.
 */
export interface LayoutModel {
  /** Sibling-order constraints in the operator's order. */
  readonly constraints: readonly OrderConstraint[]
  /** Department clusters, in the operator's order. */
  readonly clusters: readonly ClusterPlan[]
  /** Leaf id to the department cluster laying it out. */
  readonly clusterOf: ReadonlyMap<string, string>
}

export interface LayoutModelArgs {
  /** Department group nodes, in the operator's order. */
  readonly groupNodes: readonly Node[]
  /** Every node in the tree, used to resolve a leaf's owning department. */
  readonly allNodes: readonly Node[]
  /** Nodes dagre positions directly, in emission order. */
  readonly leafNodes: readonly Node[]
  readonly edges: readonly Edge[]
  readonly nodeSep: number
}

/**
 * Pick a department's internal flow from what it would cost laid out
 * top-to-bottom: one rank of `widestRankSize` cards side by side.
 */
export function chooseClusterDirection(
  widestRankSize: number,
  nodeSep: number,
): ClusterDirection {
  const stackedWidth = widestRankSize * (DEFAULT_NODE_WIDTH + nodeSep) - nodeSep
  return stackedWidth > DEPT_HORIZONTAL_WIDTH_BUDGET ? 'LR' : 'TB'
}

/** Map every leaf to the department group that ultimately contains it. */
function resolveClusterOf(args: LayoutModelArgs): Map<string, string> {
  const byId = new Map(args.allNodes.map((node) => [node.id, node]))
  const departmentIds = new Set(args.groupNodes.map((node) => node.id))
  const clusterOf = new Map<string, string>()
  for (const leaf of args.leafNodes) {
    let ancestor = leaf.parentId
    // A leaf can sit inside a team box, which sits inside the department, so
    // walk up until a department is reached rather than reading parentId once.
    // The `seen` set bounds the walk: parentId comes from server data, and a
    // cycle in it would otherwise spin here with the canvas already mounted.
    const seen = new Set<string>()
    while (ancestor !== undefined && !departmentIds.has(ancestor) && !seen.has(ancestor)) {
      seen.add(ancestor)
      ancestor = byId.get(ancestor)?.parentId
    }
    if (ancestor !== undefined && departmentIds.has(ancestor)) {
      clusterOf.set(leaf.id, ancestor)
    }
  }
  return clusterOf
}

/** Members with no parent inside `memberIds`, keeping the given order. */
function rootsWithin(
  memberIds: readonly string[],
  edges: readonly Edge[],
): string[] {
  const members = new Set(memberIds)
  const parented = new Set<string>()
  for (const edge of edges) {
    if (members.has(edge.source) && members.has(edge.target)) parented.add(edge.target)
  }
  return memberIds.filter((id) => !parented.has(id))
}

/**
 * Same-rank sibling sets within one coordinate frame, each in the given order.
 *
 * Two members reached from the same parent over edges of equal minlen land on
 * the same rank, and members with no parent in the frame are its first rank.
 * That is the whole basis for ordering without a second layout pass: the chart
 * is a tree, so an edge of a given minlen always lands its target exactly that
 * far below its parent.
 */
function sameRankSets(
  memberIds: readonly string[],
  edges: readonly Edge[],
): string[][] {
  const members = new Set(memberIds)
  const order = new Map(memberIds.map((id, index) => [id, index]))
  const buckets = new Map<string, string[]>()
  for (const edge of edges) {
    if (!members.has(edge.source) || !members.has(edge.target)) continue
    const key = `${edge.source} ${String(dagreEdgeMinlen(edge))}`
    const bucket = buckets.get(key) ?? []
    if (!bucket.includes(edge.target)) bucket.push(edge.target)
    buckets.set(key, bucket)
  }
  const sets = [rootsWithin(memberIds, edges), ...buckets.values()]
  for (const set of sets) set.sort((a, b) => order.get(a)! - order.get(b)!)
  return sets
}

function chain(ids: readonly string[]): OrderConstraint[] {
  const links: OrderConstraint[] = []
  for (let index = 1; index < ids.length; index++) {
    links.push({ left: ids[index - 1]!, right: ids[index]! })
  }
  return links
}

/**
 * Edges between coordinate frames, with each endpoint replaced by the cluster
 * that swallows it. dagre proxies cross-cluster edges the same way when it
 * lays out the parent graph, so ordering the clusters means ordering these.
 */
function clusterLevelEdges(
  edges: readonly Edge[],
  clusterOf: ReadonlyMap<string, string>,
): Edge[] {
  const seen = new Set<string>()
  const lifted: Edge[] = []
  for (const edge of edges) {
    const source = clusterOf.get(edge.source) ?? edge.source
    const target = clusterOf.get(edge.target) ?? edge.target
    if (source === target) continue
    const key = `${source} ${target}`
    if (seen.has(key)) continue
    seen.add(key)
    lifted.push({ ...edge, id: key, source, target })
  }
  return lifted
}

function buildClusters(
  args: LayoutModelArgs,
  clusterOf: ReadonlyMap<string, string>,
): ClusterPlan[] {
  const plans: ClusterPlan[] = []
  for (const group of args.groupNodes) {
    const memberIds = args.leafNodes
      .filter((leaf) => clusterOf.get(leaf.id) === group.id)
      .map((leaf) => leaf.id)
    if (memberIds.length === 0) continue
    const widestRank = Math.max(
      ...sameRankSets(memberIds, args.edges).map((set) => set.length),
    )
    plans.push({
      id: group.id,
      memberIds,
      direction: chooseClusterDirection(widestRank, args.nodeSep),
    })
  }
  return plans
}

/**
 * Derive the layout model from the emitted org tree.
 *
 * Emission order IS the operator's order: `build-org-tree` walks
 * `config.departments`, and `groupAgentsByDept` keeps each department's slice
 * of the flat `config.agents` array intact. Both arrays are what the
 * `reorder-departments` / `reorder-agents` endpoints persist, so pinning the
 * chart to emission order makes it agree with the Org Edit page instead of
 * with whatever left-to-right slots dagre's barycentre pass happened to pick.
 *
 * Constraints are emitted per coordinate frame: once inside each department,
 * once over the clusters and the nodes outside them. A constraint whose
 * endpoints sit on different ranks does not simply go unused, it enters the
 * shared constraint graph and drags unrelated pairs out of order with it, so
 * only sets proven same-rank by the tree's shape are chained.
 */
export function deriveLayoutModel(args: LayoutModelArgs): LayoutModel {
  const clusterOf = resolveClusterOf(args)
  const clusters = buildClusters(args, clusterOf)

  const constraints: OrderConstraint[] = []
  for (const cluster of clusters) {
    for (const set of sameRankSets(cluster.memberIds, args.edges)) {
      constraints.push(...chain(set))
    }
  }
  const topLevelIds = [
    ...args.leafNodes.filter((leaf) => !clusterOf.has(leaf.id)).map((leaf) => leaf.id),
    ...clusters.map((cluster) => cluster.id),
  ]
  for (const set of sameRankSets(topLevelIds, clusterLevelEdges(args.edges, clusterOf))) {
    constraints.push(...chain(set))
  }
  return { constraints, clusters, clusterOf }
}
