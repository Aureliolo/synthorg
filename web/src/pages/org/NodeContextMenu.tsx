import { useCallback, useRef, useEffect, useMemo } from 'react'
import { Pencil, Trash2, UserPlus, ArrowRightLeft, Eye } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { useViewportSize } from '@/hooks/useViewportSize'

interface NodeContextMenuProps {
  nodeId: string
  nodeType: 'agent' | 'ceo' | 'department'
  position: { x: number; y: number }
  onClose: () => void
  onViewDetails?: (nodeId: string) => void
  onDelete?: (nodeId: string) => void
}

interface MenuItem {
  label: string
  icon: React.ElementType
  action: () => void
  variant?: 'default' | 'destructive'
}

export function NodeContextMenu({
  nodeId,
  nodeType,
  position,
  onClose,
  onViewDetails,
  onDelete,
}: NodeContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  const addToast = useToastStore((s) => s.add)

  const stubAction = useCallback(
    (action: string) => {
      addToast({
        variant: 'info',
        title: `${action} -- not yet available`,
        description: 'Backend API for this operation is pending',
      })
      onClose()
    },
    [addToast, onClose],
  )

  // Close on outside click or Escape
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [onClose])

  const agentItems: MenuItem[] = [
    {
      label: 'View Details',
      icon: Eye,
      action: () => {
        onViewDetails?.(nodeId)
        onClose()
      },
    },
    { label: 'Edit Agent', icon: Pencil, action: () => stubAction('Edit Agent') },
    { label: 'Assign to Department', icon: ArrowRightLeft, action: () => stubAction('Assign to Department') },
    {
      label: 'Remove Agent',
      icon: Trash2,
      variant: 'destructive',
      action: () => {
        onDelete?.(nodeId)
        onClose()
      },
    },
  ]

  const departmentItems: MenuItem[] = [
    { label: 'Edit Department', icon: Pencil, action: () => stubAction('Edit Department') },
    { label: 'Add Agent', icon: UserPlus, action: () => stubAction('Add Agent') },
    {
      label: 'Delete Department',
      icon: Trash2,
      variant: 'destructive',
      action: () => {
        onDelete?.(nodeId)
        onClose()
      },
    },
  ]

  const ceoItems: MenuItem[] = [
    {
      label: 'View Details',
      icon: Eye,
      action: () => {
        onViewDetails?.(nodeId)
        onClose()
      },
    },
  ]

  const items =
    nodeType === 'department' ? departmentItems : nodeType === 'ceo' ? ceoItems : agentItems

  // Clamp menu position to viewport bounds. ``useViewportSize`` keeps
  // the clamp reactive to resizes without touching ``window`` directly
  // in the render body.
  const menuWidth = 180
  const menuItemHeight = 32
  const menuPadding = 8
  const menuHeight = items.length * menuItemHeight + menuPadding
  const margin = 8
  const viewport = useViewportSize()
  const boundedPosition = useMemo(() => ({
    x: Math.max(margin, Math.min(position.x, viewport.width - menuWidth - margin)),
    y: Math.max(margin, Math.min(position.y, viewport.height - menuHeight - margin)),
  }), [position.x, position.y, menuHeight, viewport.width, viewport.height])

  const menuLabel =
    nodeType === 'department' ? 'Department actions' : nodeType === 'ceo' ? 'CEO actions' : 'Agent actions'

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[180px] rounded-lg border border-border bg-card p-1 shadow-lg"
      style={{ top: boundedPosition.y, left: boundedPosition.x }}
      role="menu"
      aria-label={menuLabel}
      data-testid="node-context-menu"
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          onClick={item.action}
          role="menuitem"
          className={cn(
            'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs',
            'hover:bg-card-hover transition-colors',
            item.variant === 'destructive' ? 'text-danger hover:bg-danger/10' : 'text-foreground',
          )}
        >
          <item.icon className="size-3.5" aria-hidden="true" />
          {item.label}
        </button>
      ))}
    </div>
  )
}
