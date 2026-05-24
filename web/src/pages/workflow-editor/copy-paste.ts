/**
 * Copy/paste utilities for workflow editor node groups.
 *
 * Copies selected nodes and their internal edges (edges where both
 * source and target are in the selection). On paste, all IDs are
 * remapped and positions offset to make the paste visually distinct.
 */

import type { Edge, Node } from '@xyflow/react'

const PASTE_OFFSET = 50

export interface ClipboardData {
  readonly nodes: readonly Node[]
  readonly edges: readonly Edge[]
}

/**
 * Copy selected nodes and their internal edges.
 *
 * @param selectedIds - IDs of selected nodes
 * @param allNodes - All nodes in the graph
 * @param graphEdges - All edges in the graph
 * @returns Clipboard data with only the selected nodes and internal edges
 */
export function copyNodes(
  selectedIds: ReadonlySet<string>,
  allNodes: readonly Node[],
  graphEdges: readonly Edge[],
): ClipboardData | null {
  if (selectedIds.size === 0) return null

  const nodes = allNodes.filter((n) => selectedIds.has(n.id))
  const edges = graphEdges.filter(
    (e) => selectedIds.has(e.source) && selectedIds.has(e.target),
  )

  return { nodes, edges }
}

/**
 * Paste clipboard data with remapped IDs and offset positions.
 *
 * @param clipboard - Previously copied clipboard data
 * @returns New nodes and edges ready to be added to the graph
 */
export function pasteFromClipboard(
  clipboard: ClipboardData,
): { nodes: Node[]; edges: Edge[] } {
  const idMap = new Map<string, string>()

  for (const node of clipboard.nodes) {
    idMap.set(node.id, `${node.id}-copy-${crypto.randomUUID().slice(0, 8)}`)
  }

  const nodes = clipboard.nodes.map((node) => ({
    ...node,
    id: idMap.get(node.id)!,
    position: {
      x: node.position.x + PASTE_OFFSET,
      y: node.position.y + PASTE_OFFSET,
    },
    selected: true,
  }))

  const edges = clipboard.edges.map((edge) => ({
    ...edge,
    id: `${edge.id}-copy-${crypto.randomUUID().slice(0, 8)}`,
    source: idMap.get(edge.source) ?? edge.source,
    target: idMap.get(edge.target) ?? edge.target,
  }))

  return { nodes, edges }
}
