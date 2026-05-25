import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface SidebarNavGroupProps {
  /** Items wrapped in the vertical-stack list. */
  children: ReactNode
  className?: string
}

/**
 * Vertical stack wrapper for a group of {@link SidebarNavItem}s
 * inside a {@link SidebarSection}. Wraps the items in a flex column
 * with the canonical gap so every section uses identical spacing.
 */
export function SidebarNavGroup({ children, className }: SidebarNavGroupProps) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      {children}
    </div>
  )
}
