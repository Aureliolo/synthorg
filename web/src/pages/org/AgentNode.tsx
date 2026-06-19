import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { Avatar } from '@/components/ui/avatar'
import { StatusBadge } from '@/components/ui/status-badge'
import { cn, getStatusColor } from '@/lib/utils'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import type { AgentNodeData } from './build-org-tree'

export type AgentNodeType = Node<AgentNodeData, 'agent'>

const STATUS_RING_CLASSES: Record<string, string> = {
  success: 'ring-success/40',
  accent: 'ring-accent/20',
  warning: 'ring-warning/40',
  danger: 'ring-danger/40',
  'text-secondary': 'ring-border',
}

/*
 * Handles (source / target connection points on each node) are
 * rendered transparent by default and only appear on hover of the
 * parent node.  In the dashboard's read-only "view the org chart"
 * context the operator is never dragging to create new edges, so
 * the dots just added visual noise -- especially on nodes without
 * any incoming/outgoing connection.  Group-hover makes them visible
 * when the user focuses a specific node, which is the only moment
 * they are ever relevant.
 */
const HANDLE_CLASSES = cn(
  '!size-1.5 !border-0 !bg-border-bright',
  '!opacity-0 group-hover/agent:!opacity-100',
  'transition-opacity duration-150',
)

function AgentNodeComponent({ data }: NodeProps<AgentNodeType>) {
  const statusColor = getStatusColor(data.runtimeStatus)
  const isActive = data.runtimeStatus === 'active'
  const isOffline = data.runtimeStatus === 'offline'
  const showLeadBadge = useOrgChartPrefs((s) => s.showLeadBadge)

  return (
    <div
      className={cn(
        'group/agent relative rounded-lg border border-border bg-card p-card',
        'min-w-36 max-w-44',
        'ring-1 transition-all duration-200',
        'hover:shadow-[var(--so-shadow-card-hover)] hover:ring-2',
        STATUS_RING_CLASSES[statusColor] ?? 'ring-border',
        isOffline && 'opacity-50',
      )}
      data-testid="agent-node"
      aria-label={`Agent: ${data.name}, ${data.role}, ${data.runtimeStatus}${data.isDeptLead ? ', department lead' : ''}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className={HANDLE_CLASSES}
        isConnectable={false}
        aria-hidden="true"
      />

      {/* LEAD badge on the highest-seniority agent in the dept,
          derived from build-org-tree's `isDeptLead` flag.  Hidden
          when the user disables LEAD badges in the view menu. */}
      {data.isDeptLead && showLeadBadge && (
        <span
          className="absolute -right-1 -top-1.5 rounded-full border border-accent/60 bg-accent px-1.5 py-0 font-mono text-micro font-bold uppercase leading-4 tracking-wider text-background shadow-sm"
          aria-label="Department lead"
        >
          Lead
        </span>
      )}

      <div className="flex items-center gap-2">
        <Avatar name={data.name} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-sans text-xs font-semibold text-foreground">
              {data.name}
            </span>
            <StatusBadge
              status={data.runtimeStatus}
              pulse={isActive || data.runtimeStatus === 'error'}
              decorative
            />
          </div>
          <span className="block truncate font-sans text-micro text-muted-foreground">
            {data.role}
          </span>
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className={HANDLE_CLASSES}
        isConnectable={false}
        aria-hidden="true"
      />
    </div>
  )
}

export const AgentNode = memo(AgentNodeComponent)
