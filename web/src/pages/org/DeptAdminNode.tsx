import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { Shield } from 'lucide-react'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { DEPT_ADMIN_WIDTH } from './build-org-tree'

export interface DeptAdminNodeData extends Record<string, unknown> {
  adminId: string
  displayName: string
  department: string
  role: 'department_admin'
}

export type DeptAdminNodeType = Node<DeptAdminNodeData, 'deptAdmin'>

/**
 * Department admin node rendered inside a department box.
 *
 * Visually distinct from agent nodes and owner nodes:
 * - Shield icon badge (authority within the department)
 * - Info-blue accent border (vs amber for owners)
 * - "Dept Admin" micro-label above the name
 * - Compact footprint to fit inside dept group boxes
 */
function DeptAdminNodeComponent({ data }: NodeProps<DeptAdminNodeType>) {
  return (
    <div
      className={cn(
        'relative rounded-lg border-2 border-info/50 bg-card p-card',
        'shadow-[var(--so-shadow-card)]',
      )}
      style={{ width: DEPT_ADMIN_WIDTH }}
      data-testid="dept-admin-node"
      aria-label={`Department Admin: ${data.displayName}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1.5 !border-0 !bg-info"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1.5 !border-0 !bg-info"
      />

      <div className="mb-0.5 flex items-center justify-center gap-1 font-sans text-micro font-medium tracking-wide text-info">
        <Shield className="size-3" aria-hidden="true" />
        Dept Admin
      </div>

      <div className="flex items-center gap-2">
        <Avatar name={data.displayName} size="sm" borderColor="border-info/50" />
        <span className="min-w-0 flex-1 truncate font-sans text-xs font-semibold text-foreground">
          {data.displayName}
        </span>
      </div>
    </div>
  )
}

export const DeptAdminNode = memo(DeptAdminNodeComponent)
