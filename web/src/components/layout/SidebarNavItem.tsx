import type { LucideIcon } from 'lucide-react'
import { NavLink, useLocation } from 'react-router'
import { cn } from '@/lib/utils'

interface SidebarNavItemProps {
  to: string
  icon: LucideIcon
  label: string
  collapsed: boolean
  badge?: number | undefined
  dotColor?: string | undefined
  end?: boolean | undefined
  /**
   * Routes that must NOT highlight this item even when NavLink's default
   * `isActive` prefix-match would normally treat them as active. Used when
   * a child route has its own sidebar entry and we don't want both
   * parent and child to render in the active state simultaneously.
   *
   * Each entry is matched as a path prefix against the current location.
   */
  inactivePaths?: readonly string[]
  /** Render as a plain `<a href>` instead of a React Router NavLink. */
  external?: boolean
}

function _isForcedInactive(
  pathname: string,
  inactivePaths: readonly string[] | undefined,
): boolean {
  if (inactivePaths === undefined) return false
  return inactivePaths.some((p) => pathname === p || pathname.startsWith(`${p}/`))
}

export type SidebarNavItemContentProps = Pick<
  SidebarNavItemProps,
  'icon' | 'label' | 'collapsed' | 'badge' | 'dotColor'
>

function SidebarNavItemContent({
  icon: Icon,
  label,
  collapsed,
  badge,
  dotColor,
}: SidebarNavItemContentProps) {
  return (
    <>
      <Icon className="size-5 shrink-0" aria-hidden="true" />
      {!collapsed && (
        <>
          <span className="flex-1 truncate">{label}</span>
          <span aria-live="polite" className="contents">
            {badge !== undefined && badge > 0 && (
              <span
                aria-label={`${badge} pending ${label.toLowerCase()}`}
                className={cn(
                  'flex size-5 items-center justify-center',
                  'rounded-full bg-danger',
                  'text-xs font-semibold text-white',
                )}
              >
                {badge > 99 ? '99+' : badge}
              </span>
            )}
          </span>
          {dotColor && (
            <span className={cn('size-2 rounded-full', dotColor)} aria-hidden="true" />
          )}
        </>
      )}
    </>
  )
}

export function SidebarNavItem({
  to,
  icon,
  label,
  collapsed,
  badge,
  dotColor,
  end,
  inactivePaths,
  external,
}: SidebarNavItemProps) {
  const { pathname } = useLocation()
  const forcedInactive = _isForcedInactive(pathname, inactivePaths)
  const baseClass = cn(
    'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
    'text-text-secondary hover:bg-card-hover hover:text-foreground',
    collapsed && 'justify-center px-0',
  )
  const content = (
    <SidebarNavItemContent
      icon={icon}
      label={label}
      collapsed={collapsed}
      badge={badge}
      dotColor={dotColor}
    />
  )

  if (external) {
    // When collapsed the visible label is hidden, so the sr-only span
    // has to carry both the destination name and the new-tab hint.
    // Expanded renders the label visibly, so only the hint is needed.
    const srText = collapsed ? `${label} (opens in new tab)` : '(opens in new tab)'
    return (
      <a
        href={to}
        title={collapsed ? label : undefined}
        className={baseClass}
        target="_blank"
        rel="noopener noreferrer"
      >
        {content}
        <span className="sr-only">{srText}</span>
      </a>
    )
  }

  return (
    <NavLink
      to={to}
      end={end ?? false}
      {...(collapsed ? { title: label } : {})}
      className={({ isActive }) =>
        cn(baseClass, isActive && !forcedInactive && 'bg-card text-accent')
      }
    >
      {content}
    </NavLink>
  )
}
