import {
  Graph,
  type EdgeLabel,
  type GraphLabel,
  type NodeLabel,
  type OrderConstraint,
  type Point,
  layout,
} from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import { type LayoutDirection, getNodeDim } from './layout-shared'

export interface DagreParams {
  direction: LayoutDirection
  nodeSep: number
  rankSep: number
}

/**
 * Centre dagre assigned a node.
 *
 * The label types x/y optional because it exists before the run assigns them.
 * A node the run left unpositioned would have to be placed at a made-up origin
 * on top of its siblings, so it fails loud into the page's error boundary
 * instead of silently stacking cards.
 */
function centreOf(label: NodeLabel, id: string): Point {
  const { x, y } = label
  if (x === undefined || y === undefined) {
    throw new Error(`dagre left node "${id}" unpositioned`)
  }
  return { x, y }
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
  constraints: readonly OrderConstraint[],
): Map<string, Node> {
  const g = new Graph<GraphLabel, NodeLabel, EdgeLabel>()
  g.setGraph({ rankdir: params.direction, nodesep: params.nodeSep, ranksep: params.rankSep })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of nodes) {
    const { w, h } = getNodeDim(node)
    g.setNode(node.id, { width: w, height: h })
  }
  // Every edge takes dagre's default rank distance of one. A wider distance
  // would only insert an empty rank: the frame lays out pre-sized boxes and
  // the gaps between them are set afterwards by the shift passes.
  for (const edge of edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target)
    }
  }

  // `useDynamic` is set explicitly rather than left at its default. dagre
  // holds the previous run's graph and node collection in module-level state
  // and only clears them on an explicit `false`, so the default couples every
  // layout to whatever ran before it: the retained graph switches
  // cycle-breaking to a variant that reverses any edge whose endpoints the
  // PREVIOUS graph ranked the other way round, and the retained nodes reorder
  // long-edge dummies. Neither buys anything here (every frame is a tree, and
  // real-node order is pinned by the constraints) while both would make the
  // result depend on which unit happened to be laid out first.
  layout(g, { useDynamic: false, constraints: [...constraints] })

  // dagre returns centre coords; React Flow uses top-left.
  const positioned = new Map<string, Node>()
  for (const node of nodes) {
    const laidOut = g.node(node.id)
    const centre = centreOf(laidOut, node.id)
    positioned.set(node.id, {
      ...node,
      position: {
        x: centre.x - laidOut.width / 2,
        y: centre.y - laidOut.height / 2,
      },
    })
  }
  return positioned
}
