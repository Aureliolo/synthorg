import { Graph, layout } from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import type { LayoutModel } from './layout-clusters'
import {
  type LayoutDirection,
  dagreEdgeMinlen,
  getNodeDim,
} from './layout-shared'

export interface DagreParams {
  direction: LayoutDirection
  nodeSep: number
  rankSep: number
}

/**
 * Add one dagre cluster per populated department and parent its members to it.
 *
 * A cluster carrying a `rankdir` is laid out in isolation and placed in the
 * parent graph as a single box, which is what lets a department flow in its
 * own direction. Its width and height are recomputed by dagre from whatever
 * ends up inside, so the placeholder size here is never read back.
 */
function addClusters(
  g: Graph,
  leafNodes: readonly Node[],
  model: LayoutModel,
): void {
  for (const cluster of model.clusters) {
    g.setNode(cluster.id, { rankdir: cluster.direction, width: 0, height: 0 })
  }
  for (const node of leafNodes) {
    const { w, h } = getNodeDim(node)
    g.setNode(node.id, { width: w, height: h })
    const clusterId = model.clusterOf.get(node.id)
    if (clusterId !== undefined) g.setParent(node.id, clusterId)
  }
}

/** Run dagre over the org tree and return its leaves positioned (top-left). */
export function runDagreLayout(
  leafNodes: Node[],
  edges: Edge[],
  model: LayoutModel,
  params: DagreParams,
): Map<string, Node> {
  const g = new Graph({ compound: true })
  g.setGraph({ rankdir: params.direction, nodesep: params.nodeSep, ranksep: params.rankSep })
  g.setDefaultEdgeLabel(() => ({}))

  addClusters(g, leafNodes, model)

  // Department-to-department edges now have both endpoints in the graph, and
  // they carry the hierarchy for a department with no declared head, which has
  // no agent-level edge to be ranked by.
  for (const edge of edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target, { minlen: dagreEdgeMinlen(edge) })
    }
  }

  // `useDynamic` is set explicitly rather than left at its default. dagre
  // keeps the previous graph and its node collection in module-level state
  // and only clears them on an explicit `false`, so the default silently
  // couples one layout to whatever ran before it. What that state buys is
  // narrow -- its comparator ignores every node that is not a long-edge dummy
  // -- while the ordering that actually matters here is pinned by the
  // constraints, which make the layout a pure function of the org data.
  // eslint-disable-next-line @typescript-eslint/no-unsafe-argument -- @dagrejs/dagre types Graph with `any` generics; g is the valid dagre Graph constructed above
  layout(g, { useDynamic: false, constraints: [...model.constraints] })

  // Map positioned leaf nodes (dagre returns center coords; RF uses top-left).
  const positionedLeafMap = new Map<string, Node>()
  for (const node of leafNodes) {
    const dagreNode = g.node(node.id) as { x: number; y: number; width: number; height: number }
    positionedLeafMap.set(node.id, {
      ...node,
      position: {
        x: dagreNode.x - dagreNode.width / 2,
        y: dagreNode.y - dagreNode.height / 2,
      },
    })
  }
  return positionedLeafMap
}
