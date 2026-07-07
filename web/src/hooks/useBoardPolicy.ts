import { useCallback, useEffect, useState } from 'react'
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

export interface BoardPolicyResult {
  /** The fetched policy, or ``null`` until the first fetch resolves / on error. */
  policy: BoardPolicy | null
  /** Re-fetch the board policy so occupancy badges stay live after a move. */
  refresh: () => void
}

/**
 * Fetch the org's board WIP policy (limits + enforcement + per-column
 * occupancy) from the backend. ``policy`` is ``null`` until the first
 * fetch resolves; a failed fetch degrades to ``null`` (the board renders
 * with no WIP badges rather than erroring). ``refresh`` re-fetches so the
 * occupancy counts stay live after a card moves.
 *
 * Fetching is gated on ``enabled`` (board view) so the list view does not
 * issue a board request it never displays.
 */
export function useBoardPolicy(enabled: boolean): BoardPolicyResult {
  const [policy, setPolicy] = useState<BoardPolicy | null>(null)

  const load = useCallback((signal: { active: boolean }) => {
    void getBoard()
      .then((view) => {
        if (signal.active) setPolicy(toPolicy(view.columns, view.enforce_wip))
      })
      .catch((err: unknown) => {
        log.warn('board policy fetch failed', sanitizeForLog(err))
      })
  }, [])

  useEffect(() => {
    if (!enabled) return undefined
    const signal = { active: true }
    load(signal)
    return () => {
      signal.active = false
    }
  }, [enabled, load])

  const refresh = useCallback(() => {
    if (enabled) load({ active: true })
  }, [enabled, load])

  return { policy, refresh }
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
