import { Graph, layout } from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import { deriveConstraints } from './layout-clusters'
import { type ClusterDirection, dagreEdgeMinlen, getNodeDim } from './layout-shared'

export interface DagreParams {
  direction: ClusterDirection
  nodeSep: number
  rankSep: number
}

/**
 * Lay one frame out with dagre and return its nodes positioned (top-left).
 *
 * Every frame is a plain graph. dagre's own compound clusters are deliberately
 * not used: `recursiveClusterLayout` reads only `rankdir` off a cluster node,
 * ignoring the `ranksep`, `nodesep` and `align` overrides its types declare,
 * and it copies only a cluster's direct children into the isolated subgraph,
 * so a cluster inside a cluster loses its members entirely. Running one graph
 * per unit and inserting the result as a pre-sized box gives every unit its
 * own direction AND its own separations, and nests to any depth.
 */
export function runDagreLayout(
  nodes: readonly Node[],
  edges: readonly Edge[],
  params: DagreParams,
): Map<string, Node> {
  const g = new Graph()
  g.setGraph({ rankdir: params.direction, nodesep: params.nodeSep, ranksep: params.rankSep })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of nodes) {
    const { w, h } = getNodeDim(node)
    g.setNode(node.id, { width: w, height: h })
  }
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
  layout(g, {
    useDynamic: false,
    constraints: deriveConstraints(nodes.map((n) => n.id), edges),
  })

  // dagre returns centre coords; React Flow uses top-left.
  const positioned = new Map<string, Node>()
  for (const node of nodes) {
    const laidOut = g.node(node.id) as { x: number; y: number; width: number; height: number }
    positioned.set(node.id, {
      ...node,
      position: { x: laidOut.x - laidOut.width / 2, y: laidOut.y - laidOut.height / 2 },
    })
  }
  return positioned
}
