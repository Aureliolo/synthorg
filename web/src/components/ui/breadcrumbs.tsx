import { ChevronRight, MoreHorizontal } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'

export interface BreadcrumbItem {
  label: string
  /** React Router path. Omit on the final (current) item. */
  to?: string
}

export interface BreadcrumbsProps {
  items: readonly BreadcrumbItem[]
  /** Collapse middle items with an ellipsis when length exceeds this threshold. Default 4. */
  maxItems?: number
  className?: string
}

// maxItems must leave room for first + ellipsis + at least one tail item.
const MIN_MAX_ITEMS = 3

type RenderItem = BreadcrumbItem | 'ellipsis'

interface BreadcrumbNodeProps {
  item: RenderItem
  isLast: boolean
}

function BreadcrumbNode({ item, isLast }: BreadcrumbNodeProps) {
  if (item === 'ellipsis') {
    return (
      <span aria-hidden="true" className="inline-flex items-center">
        <MoreHorizontal className="size-3.5" />
      </span>
    )
  }
  if (isLast) {
    return (
      <span aria-current="page" className="font-medium text-foreground">
        {item.label}
      </span>
    )
  }
  if (item.to) {
    return (
      <Link
        to={item.to}
        className="rounded px-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {item.label}
      </Link>
    )
  }
  return <span>{item.label}</span>
}

interface BreadcrumbRowProps {
  item: RenderItem
  isLast: boolean
}

function BreadcrumbRow({ item, isLast }: BreadcrumbRowProps) {
  return (
    <>
      <BreadcrumbNode item={item} isLast={isLast} />
      {!isLast && (
        <ChevronRight aria-hidden="true" className="size-3 shrink-0 text-muted-foreground/70" />
      )}
    </>
  )
}

/**
 * Breadcrumb navigation for deep detail pages.
 *
 * Uses React Router's `<Link>` for clickable ancestors and a plain `<span>`
 * for the terminal (current) item, with `aria-current="page"`. Wraps in
 * `<nav aria-label="Breadcrumb">` per WAI-ARIA authoring practices.
 *
 * When `items.length > maxItems`, middle items are collapsed into an
 * ellipsis node so the trail never wraps over two lines on narrow viewports.
 *
 * **Depth convention**: keep visible trails to 2 or 3 items. The dashboard
 * is a flat info-architecture (every primary domain is one sidebar click
 * away), so deeper trails almost always reflect an organisational mistake
 * the user has to mentally unwind. When the natural depth grows past 3,
 * either route the user to a flatter parent or rely on `maxItems` so the
 * middle items collapse into the ellipsis node. See web/CLAUDE.md for the
 * full convention.
 */
export function Breadcrumbs({ items, maxItems = 4, className }: BreadcrumbsProps) {
  if (items.length === 0) return null

  // Guard against callers passing an absurdly low maxItems (e.g. 0/1/2) that
  // would otherwise slice away the current-page terminal item.
  const effectiveMax = Math.max(MIN_MAX_ITEMS, maxItems)
  const collapsed = items.length > effectiveMax
  const visibleItems: RenderItem[] = collapsed
    ? [items[0]!, 'ellipsis', ...items.slice(items.length - (effectiveMax - 2))]
    : [...items]

  return (
    <nav
      aria-label="Breadcrumb"
      className={cn('text-xs text-muted-foreground', className)}
    >
      <ol className="flex flex-wrap items-center gap-1.5">
        {visibleItems.map((item, idx) => (
          <li
            key={typeof item === 'string' ? `ellipsis-${idx}` : `${item.label}-${idx}`}
            className="flex items-center gap-1.5"
          >
            <BreadcrumbRow item={item} isLast={idx === visibleItems.length - 1} />
          </li>
        ))}
      </ol>
    </nav>
  )
}
