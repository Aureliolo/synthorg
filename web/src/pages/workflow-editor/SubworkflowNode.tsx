import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { Layers } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface SubworkflowNodeData extends Record<string, unknown> {
  label: string
  config: Record<string, unknown>
  selected?: boolean
  hasError?: boolean
}

export type SubworkflowNodeType = Node<SubworkflowNodeData, 'subworkflow'>

interface SubworkflowFields {
  subworkflowId: string | undefined
  version: string | undefined
}

function extractSubworkflowFields(data: SubworkflowNodeData): SubworkflowFields {
  const rawId = data.config.subworkflow_id
  const subworkflowId = typeof rawId === 'string' && rawId ? rawId : undefined
  const rawVersion = data.config.version
  const version = typeof rawVersion === 'string' && rawVersion ? rawVersion : undefined
  return { subworkflowId, version }
}

function SubworkflowNodeComponent({ data, selected }: NodeProps<SubworkflowNodeType>) {
  const { subworkflowId, version } = extractSubworkflowFields(data)
  return (
    <div
      className={cn(
        'min-w-40 max-w-56 rounded-lg border border-border bg-card p-card-tight',
        selected && 'ring-2 ring-accent',
        data.hasError && 'ring-2 ring-danger',
      )}
      data-testid="subworkflow-node"
      aria-label={`Subworkflow: ${data.label}`}
    >
      <Handle type="target" position={Position.Top} className="bg-border-bright! size-1.5!" />
      <div className="flex items-start gap-2">
        <Layers className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <span className="truncate font-sans text-xs font-semibold text-foreground">
            {data.label || 'Subworkflow'}
          </span>
          <SubworkflowIdDisplay subworkflowId={subworkflowId} version={version} />
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="bg-border-bright! size-1.5!"
      />
    </div>
  )
}

interface SubworkflowIdDisplayProps {
  subworkflowId: string | undefined
  version: string | undefined
}

function SubworkflowIdDisplay({ subworkflowId, version }: SubworkflowIdDisplayProps) {
  if (!subworkflowId) {
    return (
      <span className="block font-sans text-micro italic text-muted-foreground">
        Not configured
      </span>
    )
  }
  return (
    <div className="mt-0.5 flex items-center gap-1">
      <span className="truncate font-sans text-micro text-muted-foreground">
        {subworkflowId}
      </span>
      {version && (
        <span className="shrink-0 rounded-sm bg-accent/10 px-1 py-px text-micro font-medium text-accent">
          v{version}
        </span>
      )}
    </div>
  )
}

export const SubworkflowNode = memo(SubworkflowNodeComponent)
