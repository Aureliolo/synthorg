import { memo, useInsertionEffect, useMemo } from 'react'
import { useReducedMotion } from 'motion/react'
import { BaseEdge, getBezierPath, type EdgeProps, type Edge } from '@xyflow/react'

export interface CommunicationEdgeData {
  /** Total message count for this edge. */
  volume: number
  /** Messages per hour -- used for animation speed scaling. */
  frequency: number
  /** Max volume across all edges -- used for relative scaling. */
  maxVolume: number
  [key: string]: unknown
}

export type CommunicationEdgeType = Edge<CommunicationEdgeData, 'communication'>

const MIN_STROKE_WIDTH = 1.5
const MAX_STROKE_WIDTH = 6
const MIN_OPACITY = 0.3
const MAX_OPACITY = 0.8
const MIN_DASH_DURATION = 0.5 // seconds (fast)
const MAX_DASH_DURATION = 4 // seconds (slow)

/** Shared keyframe name -- all communication edges use the same animation, varying only duration. */
const KEYFRAME_NAME = 'comm-dash'
let keyframeInjected = false

function ensureKeyframe() {
  if (keyframeInjected || typeof document === 'undefined') return
  const style = document.createElement('style')
  style.textContent = `@keyframes ${KEYFRAME_NAME} { to { stroke-dashoffset: -24; } }`
  document.head.appendChild(style)
  keyframeInjected = true
}

function CommunicationEdgeComponent(props: EdgeProps<CommunicationEdgeType>) {
  // ``EdgeProps.data`` is optional, so a partially-populated (or absent) edge
  // payload must not yield ``undefined`` metrics that propagate as ``NaN`` into
  // the stroke/opacity/duration maths below. ``data`` is already typed by
  // ``EdgeProps<CommunicationEdgeType>``; optional-chain its fields instead
  // of casting the whole payload.
  const volume = props.data?.volume ?? 1
  const frequency = props.data?.frequency ?? 1
  const maxVolume = props.data?.maxVolume ?? 1

  const [edgePath] = getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  })

  // Scale stroke width linearly with volume ratio
  const ratio = Math.min(volume / Math.max(maxVolume, 1), 1)
  const strokeWidth = MIN_STROKE_WIDTH + ratio * (MAX_STROKE_WIDTH - MIN_STROKE_WIDTH)
  const opacity = MIN_OPACITY + ratio * (MAX_OPACITY - MIN_OPACITY)

  // Animation duration: higher frequency = faster (shorter duration)
  const dashDuration = Math.min(
    MAX_DASH_DURATION,
    Math.max(MIN_DASH_DURATION, MAX_DASH_DURATION / Math.max(frequency, 0.1)),
  )

  // Reactive hook (not a point-in-time read) so the edge re-renders when
  // the OS reduced-motion preference changes mid-session.
  const reduced = useReducedMotion() ?? false

  // Inject shared keyframe once (useInsertionEffect runs before DOM mutations)
  useInsertionEffect(() => { ensureKeyframe() }, [])

  const style = useMemo(
    () => ({
      stroke: 'var(--color-accent)',
      strokeWidth,
      strokeOpacity: opacity,
      strokeDasharray: 'var(--so-dash-wide)',
      ...(reduced ? {} : { animation: `${KEYFRAME_NAME} ${dashDuration}s linear infinite` }),
    }),
    [strokeWidth, opacity, dashDuration, reduced],
  )

  return <BaseEdge id={props.id} path={edgePath} style={style} />
}

export const CommunicationEdge = memo(CommunicationEdgeComponent)
