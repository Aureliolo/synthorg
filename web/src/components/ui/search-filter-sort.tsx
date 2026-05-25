import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface SearchFilterSortProps {
  /** Search control slot (typically `<SearchInput>`). Grows to fill available space. */
  search?: ReactNode
  /** Filter controls (typically native `<select>` elements). */
  filters?: ReactNode
  /** Sort control (typically `<SegmentedControl>` or native `<select>`). */
  sort?: ReactNode
  /** Extra trailing slot (e.g. "Batch actions" button when selections exist). */
  trailing?: ReactNode
  className?: string
}

/**
 * Layout wrapper for list-page controls (search + filters + sort).
 *
 * Renders slots in a single flex row on wide viewports and wraps to multiple
 * rows on narrow ones, keeping consistent spacing and ordering across pages.
 * This is a pure layout primitive: the actual `<input>` / `<select>` elements
 * are composed by the caller so pages keep control over filter semantics.
 */
function _isRenderable(node: ReactNode): boolean {
  return node != null && node !== false
}

export function SearchFilterSort({
  search,
  filters,
  sort,
  trailing,
  className,
}: SearchFilterSortProps) {
  return (
    <div
      role="group"
      aria-label="List controls"
      className={cn('flex flex-wrap items-center gap-3', className)}
    >
      {_isRenderable(search) && (
        <div className="min-w-48 flex-1 max-w-md">{search}</div>
      )}
      {_isRenderable(filters) && (
        <div className="flex flex-wrap items-center gap-2">{filters}</div>
      )}
      {_isRenderable(sort) && (
        <div className="flex items-center gap-2">{sort}</div>
      )}
      {_isRenderable(trailing) && (
        <div className="ml-auto flex items-center gap-2">{trailing}</div>
      )}
    </div>
  )
}
