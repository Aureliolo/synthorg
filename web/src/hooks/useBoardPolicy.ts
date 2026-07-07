import { useEffect, useState } from 'react'
import { getBoard } from '@/api/endpoints/board'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { ColumnWip } from '@/pages/tasks/TaskColumn'
import type { KanbanColumnView } from '@/api/types/board'
import type { KanbanColumnId } from '@/utils/tasks'

const log = createLogger('useBoardPolicy')

/**
 * Map the frontend board's column ids onto the backend's canonical
 * kanban columns. The frontend splits off-board work into extra
 * ``blocked`` / ``terminal`` lanes and names the review lane
 * ``in_review``; the backend board has the five flow columns only, so
 * only the flow-limited lanes (in-progress, review) carry a WIP limit.
 */
const BACKEND_COLUMN_BY_FRONTEND: Partial<Record<KanbanColumnId, string>> = {
  in_progress: 'in_progress',
  in_review: 'review',
}

export interface BoardPolicy {
  /** WIP state per frontend column id, for columns the backend limits. */
  wipByColumn: Partial<Record<KanbanColumnId, ColumnWip>>
  enforceWip: boolean
}

/**
 * Fetch the org's board WIP policy (limits + enforcement + per-column
 * occupancy) from the backend. Returns ``null`` until the first fetch
 * resolves; a failed fetch degrades to ``null`` (the board renders with
 * no WIP badges rather than erroring).
 */
export function useBoardPolicy(): BoardPolicy | null {
  const [policy, setPolicy] = useState<BoardPolicy | null>(null)
  useEffect(() => {
    let active = true
    void getBoard()
      .then((view) => {
        if (active) setPolicy(toPolicy(view.columns, view.enforce_wip))
      })
      .catch((err: unknown) => {
        log.warn('board policy fetch failed', sanitizeForLog(err))
      })
    return () => {
      active = false
    }
  }, [])
  return policy
}

function toPolicy(
  columns: readonly KanbanColumnView[],
  enforceWip: boolean,
): BoardPolicy {
  const byBackend = new Map<string, KanbanColumnView>(
    columns.map((c) => [c.column, c]),
  )
  const wipByColumn: Partial<Record<KanbanColumnId, ColumnWip>> = {}
  for (const [frontendId, backendColumn] of Object.entries(
    BACKEND_COLUMN_BY_FRONTEND,
  ) as [KanbanColumnId, string][]) {
    const view = byBackend.get(backendColumn)
    if (view && view.limit !== null) {
      wipByColumn[frontendId] = {
        count: view.count,
        limit: view.limit,
        overLimit: view.over_limit,
      }
    }
  }
  return { wipByColumn, enforceWip }
}
