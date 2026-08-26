import { useCallback, useMemo } from 'react'

import type { PaginationProps } from '@/components/ui/pagination'
import type { SelectOption } from '@/components/ui/select-field'
import { useListPagination } from '@/hooks/use-list-pagination'

import { childIndex, parentChoices } from './PlanEditor.containment'
import type { DraftItem } from './PlanEditor.types'

// Rows held on screen at once. Not a backend bound: a row is a whole form and
// its container picker offers every item in the plan, so this is what keeps
// the browser's work a function of the page rather than of the plan.
const ROWS_PER_PAGE = 20

export interface PagedRows {
  /** The rows to render, in plan order. */
  readonly shown: readonly DraftItem[]
  /** What each shown row may be moved under, aligned with `shown`. */
  readonly choices: readonly (readonly SelectOption[])[]
  /** Where `shown` starts in the whole draft list, so a row can number itself. */
  readonly firstShown: number
  /** The pager, or `undefined` when the plan fits on one page. */
  readonly pager: PaginationProps | undefined
  /** Append a row and move to the page it landed on. */
  readonly addAndFollow: () => void
}

/**
 * One page of editable rows, and everything derived per page.
 *
 * Every row is a full form carrying four inputs and three selects, one of
 * which offers every item in the plan as a container. Rendered for all of them
 * at the thousand items the backend accepts, that is around a million option
 * elements before the operator types anything. Paged, what the browser holds
 * is bounded by the page size whatever the plan's size.
 *
 * The draft list itself stays whole in the caller, which is what keeps the
 * save gate, the payload and the containment index seeing every item rather
 * than the page.
 *
 * @param drafts The whole draft list.
 * @param add Appends one empty row to it.
 * @returns The page and what the caller needs to render it.
 */
export function usePlanEditorRows(
  drafts: readonly DraftItem[],
  add: () => void,
): PagedRows {
  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize } =
    useListPagination({
      items: drafts,
      namespace: 'planItems',
      defaultPageSize: ROWS_PER_PAGE,
    })

  const children = useMemo(() => childIndex(drafts), [drafts])
  const choices = useMemo(
    () => parentChoices(drafts, children, paginatedItems),
    [drafts, children, paginatedItems],
  )

  const addAndFollow = useCallback(() => {
    // The new row is appended, which on a paged list is usually not the page
    // being read. Following it is what keeps "Add item" meaning the same thing
    // at a thousand items as at ten, rather than appearing to do nothing.
    add()
    setPage(Math.floor(drafts.length / pageSize) + 1)
  }, [add, setPage, drafts.length, pageSize])

  // Offered only where it does something: most plans fit on one page, and a
  // pager with nowhere to go is a control that reads as broken.
  const pager: PaginationProps | undefined =
    totalItems > pageSize
      ? {
          page,
          pageSize,
          total: totalItems,
          onPageChange: setPage,
          onPageSizeChange: setPageSize,
          ariaLabel: 'Plan item pages',
        }
      : undefined

  return {
    shown: paginatedItems,
    choices,
    firstShown: (page - 1) * pageSize,
    pager,
    addAndFollow,
  }
}
