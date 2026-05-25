import type { ReactNode } from 'react'

export interface SidebarSectionProps {
  /**
   * Optional uppercase label shown above the section's nav items.
   * Hidden when the sidebar is collapsed so only icons remain visible.
   */
  label?: string
  /** Sidebar-collapse state: hides label + top-border divider when true. */
  collapsed: boolean
  /** When true, render a top border + spacing above the section. */
  withTopBorder?: boolean
  /** The section's nav items (typically a {@link SidebarNavGroup}). */
  children: ReactNode
}

/**
 * Labelled sidebar section wrapper. Renders an optional uppercase
 * header (suppressed when collapsed) and an optional top divider so
 * sections compose into the existing 4-bucket sidebar layout.
 */
export function SidebarSection({
  label,
  collapsed,
  withTopBorder = false,
  children,
}: SidebarSectionProps) {
  const containerClass = withTopBorder ? 'mt-4 border-t border-border pt-3' : ''
  return (
    <div className={containerClass}>
      {label && !collapsed && (
        <span className="mb-2 block px-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      {children}
    </div>
  )
}
