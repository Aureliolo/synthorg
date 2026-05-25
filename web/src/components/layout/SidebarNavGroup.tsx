import type { ReactNode } from 'react'

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
    <div className={className ?? 'flex flex-col gap-1'}>
      {children}
    </div>
  )
}
