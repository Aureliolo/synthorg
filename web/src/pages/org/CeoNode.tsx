import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { Avatar } from '@/components/ui/avatar'
import { StatusBadge } from '@/components/ui/status-badge'
import { cn } from '@/lib/utils'
import type { CeoNodeData } from './build-org-tree'

export type CeoNodeType = Node<CeoNodeData, 'ceo'>

/*
 * Handles are transparent by default, revealed on hover to reduce
 * visual clutter in read-only mode.  Same pattern as AgentNode.
 */
const HANDLE_CLASSES = cn(
  '!size-1.5 !border-0 !bg-accent',
  '!opacity-0 group-hover/ceo:!opacity-100',
  'transition-opacity duration-150',
)

function CeoNodeComponent({ data }: NodeProps<CeoNodeType>) {
  const isActive = data.runtimeStatus === 'active'

  return (
    <div
      className={cn(
        'group/ceo relative rounded-lg border-2 border-accent/40 bg-card p-card',
        'min-w-[200px] max-w-[240px]',
        'shadow-[var(--so-shadow-card-hover)] transition-all duration-200',
        'hover:shadow-[var(--so-shadow-card-hover)]',
      )}
      data-testid="ceo-node"
      aria-label={`CEO: ${data.name}, ${data.companyName}`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE_CLASSES} />

      <div className="mb-1 text-center font-sans text-micro font-medium uppercase tracking-wider text-accent">
        {data.companyName}
      </div>

      <div className="flex items-center gap-2.5">
        <Avatar name={data.name} size="md" borderColor="border-accent/50" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-sans text-sm font-semibold text-foreground">
              {data.name}
            </span>
            <StatusBadge
              status={data.runtimeStatus}
              pulse={isActive || data.runtimeStatus === 'error'}
            />
          </div>
          <span className="block truncate font-sans text-xs text-muted-foreground">
            {data.role}
          </span>
        </div>
      </div>

      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASSES} />
    </div>
  )
}

export const CeoNode = memo(CeoNodeComponent)
