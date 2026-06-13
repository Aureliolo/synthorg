import { memo } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from '@xyflow/react'

function ConditionalEdgeComponent(props: EdgeProps) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  })

  const isTrueBranch = props.data?.['branch'] === 'true'
  const color = isTrueBranch ? 'var(--so-success)' : 'var(--so-danger)'
  const label = props.label || (isTrueBranch ? 'true' : 'false')

  return (
    <>
      <BaseEdge
        id={props.id}
        path={edgePath}
        style={{
          stroke: color,
          strokeWidth: 'var(--so-stroke-thin)',
          strokeDasharray: 'var(--so-dash-loose)',
        }}
        markerEnd={props.markerEnd}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan pointer-events-none absolute rounded bg-surface px-1 py-0.5 font-sans text-micro"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            color,
          }}
        >
          {label}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

export const ConditionalEdge = memo(ConditionalEdgeComponent)
