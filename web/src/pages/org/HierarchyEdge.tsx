import { memo } from 'react'
import { BaseEdge, type Edge, type EdgeProps } from '@xyflow/react'
import { useReducedMotion } from 'motion/react'

export interface HierarchyEdgeData {
  /** When omitted or false, the edge renders as a static line. */
  particlesVisible?: boolean
  /** Corridors from `routeHierarchyEdges`; absent before a layout has run. */
  trunkY?: number
  busY?: number
  riserX?: number
  // React Flow requires data to extend Record<string, unknown>.
  [key: string]: unknown
}

type HierarchyEdgeType = Edge<HierarchyEdgeData, 'hierarchy'>

/** Target particle speed, in pixels per second. */
const PARTICLE_PX_PER_SEC = 140
/** Minimum duration for very short edges so the particle stays visible. */
const MIN_PARTICLE_DUR_SEC = 0.8
/**
 * Bend offset used when an edge carries no routed corridors.
 *
 * Every edge sharing a source Y bends at the same absolute Y, so siblings still
 * coincide into one junction. Reached before a layout has placed the nodes, and
 * for the owner-to-root edge, which is a straight drop anyway.
 */
const BEND_OFFSET = 30

/** Manhattan length of a path's points, for a uniform particle speed. */
function pathLength(points: readonly [number, number][]): number {
  let total = 0
  for (let index = 1; index < points.length; index++) {
    total += Math.abs(points[index]![0] - points[index - 1]![0])
    total += Math.abs(points[index]![1] - points[index - 1]![1])
  }
  return total
}

interface Corridors {
  readonly trunkY: number
  readonly busY: number
  readonly riserX: number | undefined
}

/** The routed corridors, falling back to a bend below the source handle. */
function corridorsOf(props: EdgeProps<HierarchyEdgeType>): Corridors {
  const trunkY = props.data?.trunkY ?? props.sourceY + BEND_OFFSET
  return { trunkY, busY: props.data?.busY ?? trunkY, riserX: props.data?.riserX }
}

/**
 * The corners this edge turns, from its endpoints and its routed corridors.
 *
 * Three shapes. A target on the first row of its siblings drops to the shared
 * trunk, runs across, and drops in; a target on a later row takes the riser down
 * past the rows in the way first; and a target already under its source is a
 * straight drop, which avoids emitting a zero-length segment.
 */
function cornersOf(props: EdgeProps<HierarchyEdgeType>): [number, number][] {
  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty } = props
  const { trunkY, busY, riserX } = corridorsOf(props)

  if (riserX !== undefined && Math.abs(busY - trunkY) >= 0.5) {
    return [[sx, sy], [sx, trunkY], [riserX, trunkY], [riserX, busY], [tx, busY], [tx, ty]]
  }
  if (Math.abs(tx - sx) < 0.5) return [[sx, sy], [tx, ty]]
  return [[sx, sy], [sx, trunkY], [tx, trunkY], [tx, ty]]
}

/**
 * Custom orthogonal hierarchy edge used for every reporting-chain
 * line in the org chart.
 *
 * We deliberately do NOT use React Flow's `getSmoothStepPath`
 * because its fanout routing is a Z-shape (`source → down @
 * sourceGapped.y → across @ centerX → down @ targetGapped.y →
 * across → target`) that picks a different Y for each sibling
 * edge, so three children of the same parent end up with three
 * mismatched horizontal segments instead of a single shared
 * junction line.  See:
 *   node_modules/@xyflow/system/.../index.mjs → `getPoints`
 *
 * Siblings share their trunk, bus and riser exactly, so those spans are drawn
 * once per edge on top of each other. The stroke is therefore fully opaque: at
 * 0.7 alpha the overlaps composited to 0.91 and 0.97 and the shared trunk read
 * as a line with brightness steps in it, which looks like broken segments rather
 * than the single junction the coincidence is there to produce.
 */
function HierarchyEdgeComponent(props: EdgeProps<HierarchyEdgeType>) {
  const reducedMotion = useReducedMotion()
  const showParticles = !reducedMotion && (props.data?.particlesVisible ?? false)

  const corners = cornersOf(props)
  const edgePath = corners
    .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x},${y}`)
    .join(' ')
  const durSec = Math.max(
    MIN_PARTICLE_DUR_SEC,
    pathLength(corners) / PARTICLE_PX_PER_SEC,
  )

  return (
    <>
      <BaseEdge
        id={props.id}
        path={edgePath}
        style={{
          stroke: 'var(--color-border-bright)',
          strokeWidth: 1.5,
        }}
      />
      {showParticles && (
        <circle
          r="2.5"
          fill="var(--color-accent)"
          opacity="0.9"
          aria-hidden="true"
        >
          <animateMotion
            dur={`${durSec.toFixed(2)}s`}
            repeatCount="indefinite"
            path={edgePath}
          />
        </circle>
      )}
    </>
  )
}

export const HierarchyEdge = memo(HierarchyEdgeComponent)
