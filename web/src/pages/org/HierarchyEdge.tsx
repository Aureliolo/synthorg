import { memo } from 'react'
import { BaseEdge, type Edge, type EdgeProps } from '@xyflow/react'
import { useReducedMotion } from 'motion/react'
import {
  hierarchyEdgeCorners,
  hierarchyEdgePath,
  hierarchyPathLength,
  type HierarchyEdgeData,
} from './hierarchy-edge-geometry'

type HierarchyEdgeType = Edge<HierarchyEdgeData, 'hierarchy'>

/** Target particle speed, in pixels per second. */
const PARTICLE_PX_PER_SEC = 140
/** Minimum duration for very short edges so the particle stays visible. */
const MIN_PARTICLE_DUR_SEC = 0.8

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

  const corners = hierarchyEdgeCorners(props)
  const edgePath = hierarchyEdgePath(corners)
  const durSec = Math.max(
    MIN_PARTICLE_DUR_SEC,
    hierarchyPathLength(corners) / PARTICLE_PX_PER_SEC,
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
