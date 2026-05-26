import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { User } from 'lucide-react'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import type { OwnerNodeData } from './build-org-tree'

export type OwnerNodeType = Node<OwnerNodeData, 'owner'>

/**
 * Synthetic "human owner" node rendered at the very top of the org
 * chart.  Represents any user with the `owner` org role.  Distinct
 * visual treatment from agent/ceo nodes so operators can tell humans
 * from AI at a glance:
 *
 * - `User` icon badge in the corner (instead of a status dot)
 * - Amber accent border (warm human colour vs the cool accent the AI
 *   nodes use)
 * - "Owner" micro-label above the name
 * - Slightly larger footprint than an agent card so it reads as the
 *   root of the hierarchy
 */
function OwnerNodeComponent({ data }: NodeProps<OwnerNodeType>) {
  return (
    <div
      className={cn(
        'relative rounded-xl border-2 border-warning/50 bg-card p-card',
        // Fixed 240 px (not min/max) so the card's rendered width
        // matches build-org-tree's declared OWNER_NODE_WIDTH exactly.
        // Without this, React Flow measures the actual rendered
        // width (which drifts with avatar + display-name content)
        // and the centering math in layout.ts ends up ~10 px off,
        // producing a visible horizontal kink in the owner to root
        // dept edge's L-shape.
        'w-[240px]',
        'shadow-[var(--so-shadow-card)]',
      )}
      data-testid="owner-node"
      aria-label={`Owner: ${data.displayName}`}
    >
      {/*
       * Source handle at the bottom so edges can flow from the owner
       * down to the CEO.  No top handle -- the owner has nothing
       * above it in the hierarchy.
       */}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1.5 !border-0 !bg-warning"
      />

      <div className="mb-1 flex items-center justify-center gap-1 font-sans text-micro font-medium tracking-wide text-warning">
        <User className="size-3" aria-hidden="true" />
        Owner
      </div>

      <div className="flex items-center gap-2.5">
        <Avatar name={data.displayName} size="md" borderColor="border-warning/50" />
        <div className="min-w-0 flex-1">
          <span className="block truncate font-sans text-sm font-semibold text-foreground">
            {data.displayName}
          </span>
          <span className="block truncate font-sans text-xs text-text-secondary">
            {data.isCurrentUser ? 'You' : 'Owner'}
          </span>
        </div>
      </div>
    </div>
  )
}

export const OwnerNode = memo(OwnerNodeComponent)
