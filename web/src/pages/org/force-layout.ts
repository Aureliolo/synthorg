/**
 * Force-directed layout engine for the Org Chart communication graph.
 *
 * Uses d3-force to compute node positions based on communication links.
 * Higher-volume links pull connected nodes closer together.
 */

import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force'
import type { Node } from '@xyflow/react'
import type { CommunicationLink } from './aggregate-messages'

export interface ForceLayoutOptions {
  width?: number
  height?: number
}

interface SimNode extends SimulationNodeDatum {
  id: string
}

const DEFAULT_WIDTH = 800
const DEFAULT_HEIGHT = 600
const NODE_RADIUS = 80
const CHARGE_STRENGTH = -200
const TICK_COUNT = 300

// Distance range: high-volume links get shorter distance
const MAX_LINK_DISTANCE = 250
const MIN_LINK_DISTANCE = 80

/** Seed simulation nodes from the current React Flow positions. */
function toSimNodes(nodes: Node[]): SimNode[] {
  return nodes.map((n) => ({ id: n.id, x: n.position.x, y: n.position.y }))
}

/** Bidirectional source::target -> volume lookup for link distances. */
function buildVolumeMap(validLinks: CommunicationLink[]): Map<string, number> {
  const volumeMap = new Map<string, number>()
  for (const l of validLinks) {
    volumeMap.set(`${l.source}::${l.target}`, l.volume)
    volumeMap.set(`${l.target}::${l.source}`, l.volume)
  }
  return volumeMap
}

/** Higher volume = shorter link distance (inverse relationship). */
function linkDistance(
  d: SimulationLinkDatum<SimNode>,
  volumeMap: Map<string, number>,
  maxVolume: number,
): number {
  const srcId = typeof d.source === 'object' ? d.source.id : String(d.source)
  const tgtId = typeof d.target === 'object' ? d.target.id : String(d.target)
  const volume = volumeMap.get(`${srcId}::${tgtId}`) ?? 1
  const ratio = volume / maxVolume
  return MAX_LINK_DISTANCE - ratio * (MAX_LINK_DISTANCE - MIN_LINK_DISTANCE)
}

/** Map simulation results back onto the original React Flow nodes. */
function applyPositions(nodes: Node[], simNodes: SimNode[]): Node[] {
  const positionMap = new Map<string, { x: number; y: number }>()
  for (const simNode of simNodes) {
    positionMap.set(simNode.id, { x: simNode.x ?? 0, y: simNode.y ?? 0 })
  }
  return nodes.map((node) => {
    const pos = positionMap.get(node.id)
    return { ...node, position: pos ? { x: pos.x, y: pos.y } : node.position }
  })
}

/**
 * Compute force-directed layout positions for React Flow nodes.
 *
 * @param nodes - React Flow nodes (positions used as initial seed).
 * @param links - Communication links between agents.
 * @param options - Optional width/height for centering.
 * @returns New array of nodes with updated positions. Original data and IDs preserved.
 */
export function computeForceLayout(
  nodes: Node[],
  links: CommunicationLink[],
  options: ForceLayoutOptions = {},
): Node[] {
  if (nodes.length === 0) return []

  const { width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT } = options
  const nodeIdSet = new Set(nodes.map((n) => n.id))
  const simNodes = toSimNodes(nodes)
  const validLinks = links.filter(
    (l) => nodeIdSet.has(l.source) && nodeIdSet.has(l.target),
  )
  const maxVolume = Math.max(1, ...validLinks.map((l) => l.volume))
  const simLinks: SimulationLinkDatum<SimNode>[] = validLinks.map((l) => ({
    source: l.source,
    target: l.target,
  }))
  const volumeMap = buildVolumeMap(validLinks)

  const simulation = forceSimulation<SimNode>(simNodes)
    .force(
      'link',
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((d) => d.id)
        .distance((d) => linkDistance(d, volumeMap, maxVolume)),
    )
    .force('charge', forceManyBody().strength(CHARGE_STRENGTH))
    .force('center', forceCenter(width / 2, height / 2))
    .force('collide', forceCollide(NODE_RADIUS))
    .stop()

  // Run simulation to convergence synchronously.
  simulation.tick(TICK_COUNT)

  return applyPositions(nodes, simNodes)
}
