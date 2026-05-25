import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { useCallback, useId } from 'react'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/utils/format'
import { Button } from './button'

export interface PaginationProps {
  /** 1-indexed current page. */
  page: number
  /** Items per page. */
  pageSize: number
  /** Total item count (client-side). Undefined signals unknown total (cursor mode placeholder). */
  total: number | undefined
  onPageChange: (page: number) => void
  onPageSizeChange?: (size: number) => void
  /** Available page size options. Default [20, 50, 100]. */
  pageSizeOptions?: readonly number[]
  /** Hide page size selector (when total is small or fixed). */
  hidePageSize?: boolean
  /** Aria label for the nav element. Default "Pagination". */
  ariaLabel?: string
  className?: string
}

const DEFAULT_PAGE_SIZE_OPTIONS = [20, 50, 100] as const

interface PaginationGeometry {
  knownTotal: boolean
  safePageSize: number
  totalPages: number
  safePage: number
  isFirst: boolean
  isNextDisabled: boolean
  isLastJumpDisabled: boolean
  rangeStart: number | undefined
  rangeEnd: number | undefined
}

function _safePageSizeFor(pageSize: number, pageSizeOptions: readonly number[]): number {
  // Defensive clamp: pageSize must be positive to avoid division-by-zero
  // in totalPages. Fall back to the first option in the caller-supplied
  // list (rather than a fixed 20) so the <select> below always has a
  // matching <option> when `pageSize` is invalid or absent.
  const firstValidOption = pageSizeOptions.find((size) => size > 0) ?? DEFAULT_PAGE_SIZE_OPTIONS[0]!
  return pageSize > 0 ? pageSize : firstValidOption
}

function _computeRange(
  total: number | undefined,
  safePage: number,
  safePageSize: number,
): { rangeStart: number | undefined; rangeEnd: number | undefined } {
  if (total === undefined) return { rangeStart: undefined, rangeEnd: undefined }
  if (total === 0) return { rangeStart: 0, rangeEnd: 0 }
  return {
    rangeStart: (safePage - 1) * safePageSize + 1,
    rangeEnd: Math.min(safePage * safePageSize, total),
  }
}

function _computeGeometry(
  page: number,
  pageSize: number,
  total: number | undefined,
  pageSizeOptions: readonly number[],
): PaginationGeometry {
  const knownTotal = total !== undefined
  const safePageSize = _safePageSizeFor(pageSize, pageSizeOptions)
  const totalPages = knownTotal && total > 0
    ? Math.max(1, Math.ceil(total / safePageSize))
    : 1
  // In cursor mode (total unknown) we cannot determine totalPages, so
  // do not clamp down to 1.
  const safePage = knownTotal ? Math.min(Math.max(1, page), totalPages) : Math.max(1, page)
  const isFirst = safePage <= 1
  const isLastKnown = knownTotal && safePage >= totalPages
  // In cursor mode Next stays enabled (consumer controls flow); Last is
  // disabled because the total page count is not known to this control.
  const isNextDisabled = knownTotal ? isLastKnown : false
  const isLastJumpDisabled = knownTotal ? isLastKnown : true
  const { rangeStart, rangeEnd } = _computeRange(total, safePage, safePageSize)
  return {
    knownTotal, safePageSize, totalPages, safePage,
    isFirst, isNextDisabled, isLastJumpDisabled,
    rangeStart, rangeEnd,
  }
}

interface KeyAction {
  readonly disabled: boolean
  readonly nextPage: number
}

function _isShortcutOriginEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    target.isContentEditable
  )
}

function _keyActionFor(key: string, geo: PaginationGeometry): KeyAction | null {
  switch (key) {
    case 'ArrowLeft':
    case 'PageUp':
      return { disabled: geo.isFirst, nextPage: geo.safePage - 1 }
    case 'ArrowRight':
    case 'PageDown':
      return { disabled: geo.isNextDisabled, nextPage: geo.safePage + 1 }
    case 'Home':
      return { disabled: geo.isFirst, nextPage: 1 }
    case 'End':
      return { disabled: geo.isLastJumpDisabled, nextPage: geo.totalPages }
    default:
      return null
  }
}

function PageSizeSelect({
  pageSize,
  safePageSize,
  pageSizeOptions,
  onPageSizeChange,
}: {
  pageSize: number
  safePageSize: number
  pageSizeOptions: readonly number[]
  onPageSizeChange: (size: number) => void
}) {
  const selectId = useId()
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor={selectId} className="sr-only">
        Items per page
      </label>
      <select
        id={selectId}
        value={pageSizeOptions.includes(pageSize) ? pageSize : safePageSize}
        onChange={(e) => onPageSizeChange(Number(e.target.value))}
        className={cn(
          'rounded-md border border-border bg-surface px-2 py-1 text-xs text-foreground',
          'focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent',
        )}
      >
        {pageSizeOptions.map((size) => (
          <option key={size} value={size}>
            {size} / page
          </option>
        ))}
      </select>
    </div>
  )
}

function PaginationButtons({
  geo,
  total,
  onPageChange,
}: {
  geo: PaginationGeometry
  total: number | undefined
  onPageChange: (page: number) => void
}) {
  return (
    <div className="flex items-center gap-1">
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="First page"
        disabled={geo.isFirst}
        onClick={() => onPageChange(1)}
      >
        <ChevronsLeft className="size-3.5" aria-hidden="true" />
      </Button>
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Previous page"
        disabled={geo.isFirst}
        onClick={() => onPageChange(geo.safePage - 1)}
      >
        <ChevronLeft className="size-3.5" aria-hidden="true" />
      </Button>
      <span aria-current="page" className="px-2 tabular-nums text-foreground">
        {formatNumber(geo.safePage)}
        {total !== undefined && (
          <span className="text-muted-foreground"> / {formatNumber(geo.totalPages)}</span>
        )}
      </span>
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Next page"
        disabled={geo.isNextDisabled}
        onClick={() => onPageChange(geo.safePage + 1)}
      >
        <ChevronRight className="size-3.5" aria-hidden="true" />
      </Button>
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Last page"
        disabled={geo.isLastJumpDisabled}
        onClick={() => onPageChange(geo.totalPages)}
      >
        <ChevronsRight className="size-3.5" aria-hidden="true" />
      </Button>
    </div>
  )
}

function PaginationStatus({
  total,
  geo,
}: {
  total: number | undefined
  geo: PaginationGeometry
}) {
  if (total === undefined) {
    return <div className="text-muted-foreground">Page {formatNumber(geo.safePage)}</div>
  }
  if (total === 0) {
    return <div className="text-muted-foreground">No items</div>
  }
  return (
    <div className="text-muted-foreground">
      {formatNumber(geo.rangeStart ?? 0)}-{formatNumber(geo.rangeEnd ?? 0)} of {formatNumber(total)}
    </div>
  )
}

/**
 * Pagination control for list views.
 *
 * Client-side slice mode: pass `page` + `pageSize` + `total`; the caller
 * slices its own list. Keyboard shortcuts (when focused inside the nav):
 * - Left / PageUp: previous page
 * - Right / PageDown: next page
 * - Home: first page
 * - End: last page
 *
 * The component API is stable for cursor-based pagination: when OPS-1
 * ships cursor endpoints, call-sites can keep using `<Pagination>` and
 * swap the underlying data fetch without changing the control surface.
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  hidePageSize = false,
  ariaLabel = 'Pagination',
  className,
}: PaginationProps) {
  const geo = _computeGeometry(page, pageSize, total, pageSizeOptions)

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      // Swallow only shortcuts that originate on the nav buttons themselves.
      // If the user is typing in the page-size <select>, the browser's own
      // Arrow / Home / End behaviour (open/close options, jump to bounds)
      // should win, we must not hijack it.
      if (_isShortcutOriginEditable(event.target)) return
      const action = _keyActionFor(event.key, geo)
      if (!action || action.disabled) return
      event.preventDefault()
      onPageChange(action.nextPage)
    },
    [geo, onPageChange],
  )

  return (
    <nav
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn('flex flex-wrap items-center justify-between gap-3 text-xs', className)}
    >
      <PaginationStatus total={total} geo={geo} />
      <div className="flex items-center gap-2">
        {!hidePageSize && onPageSizeChange && (
          <PageSizeSelect
            pageSize={pageSize}
            safePageSize={geo.safePageSize}
            pageSizeOptions={pageSizeOptions}
            onPageSizeChange={onPageSizeChange}
          />
        )}
        <PaginationButtons geo={geo} total={total} onPageChange={onPageChange} />
      </div>
    </nav>
  )
}
