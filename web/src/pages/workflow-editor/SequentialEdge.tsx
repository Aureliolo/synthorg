import { memo } from 'react'
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@xyflow/react'

function SequentialEdgeComponent(props: EdgeProps) {
  const [edgePath] = getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  })

  return (
    <BaseEdge
      id={props.id}
      path={edgePath}
      style={{ stroke: 'var(--so-border-bright)', strokeWidth: 1.5 }}
      {...(props.markerEnd != null && { markerEnd: props.markerEnd })}
    />
  )
}

export const SequentialEdge = memo(SequentialEdgeComponent)
