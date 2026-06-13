import { useCallback, useRef, useEffect, useMemo, type RefObject } from 'react'
import { Pencil, Trash2, UserPlus, ArrowRightLeft, Eye } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { useViewportSize } from '@/hooks/useViewportSize'

interface NodeContextMenuProps {
  nodeId: string
  nodeType: 'agent' | 'ceo' | 'department'
  position: { x: number; y: number }
  onClose: () => void
  onViewDetails?: ((nodeId: string) => void) | undefined
  onDelete?: ((nodeId: string) => void) | undefined
}

interface MenuItem {
  label: string
  icon: React.ElementType
  action: () => void
  variant?: 'default' | 'destructive'
}

const MENU_WIDTH = 180
const MENU_ITEM_HEIGHT = 32
const MENU_PADDING = 8
const MENU_MARGIN = 8

interface MenuItemContext {
  nodeId: string
  onClose: () => void
  stubAction: (action: string) => void
  onViewDetails?: ((nodeId: string) => void) | undefined
  onDelete?: ((nodeId: string) => void) | undefined
}

/** Build the action list for a node type (agent / ceo / department). */
function buildMenuItems(
  nodeType: NodeContextMenuProps['nodeType'],
  ctx: MenuItemContext,
): MenuItem[] {
  const viewDetails: MenuItem = {
    label: 'View Details',
    icon: Eye,
    action: () => {
      ctx.onViewDetails?.(ctx.nodeId)
      ctx.onClose()
    },
  }
  const remove = (label: string): MenuItem => ({
    label,
    icon: Trash2,
    variant: 'destructive',
    action: () => {
      ctx.onDelete?.(ctx.nodeId)
      ctx.onClose()
    },
  })
  if (nodeType === 'department') {
    return [
      { label: 'Edit Department', icon: Pencil, action: () => ctx.stubAction('Edit Department') },
      { label: 'Add Agent', icon: UserPlus, action: () => ctx.stubAction('Add Agent') },
      remove('Delete Department'),
    ]
  }
  if (nodeType === 'ceo') return [viewDetails]
  return [
    viewDetails,
    { label: 'Edit Agent', icon: Pencil, action: () => ctx.stubAction('Edit Agent') },
    {
      label: 'Assign to Department',
      icon: ArrowRightLeft,
      action: () => ctx.stubAction('Assign to Department'),
    },
    remove('Remove Agent'),
  ]
}

/** Close the menu on an outside mousedown or the Escape key. */
function useDismissOnOutside(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
): void {
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
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
  }, [ref, onClose])
}

function menuLabelFor(nodeType: NodeContextMenuProps['nodeType']): string {
  if (nodeType === 'department') return 'Department actions'
  if (nodeType === 'ceo') return 'CEO actions'
  return 'Agent actions'
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

  useDismissOnOutside(menuRef, onClose)

  const items = buildMenuItems(nodeType, { nodeId, onClose, stubAction, onViewDetails, onDelete })

  // Clamp menu position to viewport bounds. ``useViewportSize`` keeps
  // the clamp reactive to resizes without touching ``window`` directly
  // in the render body.
  const menuHeight = items.length * MENU_ITEM_HEIGHT + MENU_PADDING
  const viewport = useViewportSize()
  const boundedPosition = useMemo(
    () => ({
      x: Math.max(MENU_MARGIN, Math.min(position.x, viewport.width - MENU_WIDTH - MENU_MARGIN)),
      y: Math.max(MENU_MARGIN, Math.min(position.y, viewport.height - menuHeight - MENU_MARGIN)),
    }),
    [position.x, position.y, menuHeight, viewport.width, viewport.height],
  )

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[180px] rounded-lg border border-border bg-card p-1 shadow-lg"
      style={{ top: boundedPosition.y, left: boundedPosition.x }}
      role="menu"
      aria-label={menuLabelFor(nodeType)}
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
