/**
 * The corners a reporting line turns, from its endpoints and its corridors.
 *
 * `route-hierarchy.ts` decides WHERE the corridors are, from the placed
 * geometry of every sibling at once. This decides which corners one edge turns
 * to use them, which is all an edge component can see.
 *
 * A separate module from the component so both are testable without rendering
 * a canvas, and so the component file exports a component and nothing else.
 */

export interface HierarchyEdgeData {
  /** When omitted or false, the edge renders as a static line. */
  particlesVisible?: boolean
  /** Corridors from `hierarchyRoutingPlan`; absent before a layout has run. */
  trunkY?: number
  busY?: number
  riserX?: number
  // React Flow requires data to extend Record<string, unknown>.
  [key: string]: unknown
}

/** Everything the geometry needs: two handles and whatever routing exists. */
export interface HierarchyEdgeGeometry {
  readonly sourceX: number
  readonly sourceY: number
  readonly targetX: number
  readonly targetY: number
  readonly data?: HierarchyEdgeData | undefined
}

/**
 * Bend offset used when an edge carries no routed corridors.
 *
 * Every edge sharing a source Y bends at the same absolute Y, so siblings still
 * coincide into one junction. Reached before a layout has placed the nodes, and
 * for the owner-to-root edge, which is a straight drop anyway.
 */
const BEND_OFFSET = 30

/** Two coordinates count as the same corridor inside this many pixels. */
const COINCIDENT = 0.5

interface Corridors {
  readonly trunkY: number
  readonly busY: number
  readonly riserX: number | undefined
}

/** The routed corridors, falling back to a bend below the source handle. */
function corridorsOf(edge: HierarchyEdgeGeometry): Corridors {
  const trunkY = edge.data?.trunkY ?? edge.sourceY + BEND_OFFSET
  return { trunkY, busY: edge.data?.busY ?? trunkY, riserX: edge.data?.riserX }
}

/**
 * The corners this edge turns.
 *
 * Three shapes. A target on the first row of its siblings drops to the shared
 * trunk, runs across, and drops in; a target on a later row takes the riser down
 * past the rows in the way first; and a target already under its source is a
 * straight drop, which avoids emitting a zero-length segment.
 */
export function hierarchyEdgeCorners(
  edge: HierarchyEdgeGeometry,
): [number, number][] {
  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty } = edge
  const { trunkY, busY, riserX } = corridorsOf(edge)

  if (riserX !== undefined && Math.abs(busY - trunkY) >= COINCIDENT) {
    return [
      [sx, sy],
      [sx, trunkY],
      [riserX, trunkY],
      [riserX, busY],
      [tx, busY],
      [tx, ty],
    ]
  }
  if (Math.abs(tx - sx) < COINCIDENT) {
    return [
      [sx, sy],
      [tx, ty],
    ]
  }
  return [
    [sx, sy],
    [sx, trunkY],
    [tx, trunkY],
    [tx, ty],
  ]
}

/** Manhattan length of a path's points, for a uniform particle speed. */
export function hierarchyPathLength(points: readonly [number, number][]): number {
  let total = 0
  for (let index = 1; index < points.length; index++) {
    total += Math.abs(points[index]![0] - points[index - 1]![0])
    total += Math.abs(points[index]![1] - points[index - 1]![1])
  }
  return total
}

/** The SVG `d` attribute for a corner list. */
export function hierarchyEdgePath(corners: readonly [number, number][]): string {
  return corners
    .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x},${y}`)
    .join(' ')
}
