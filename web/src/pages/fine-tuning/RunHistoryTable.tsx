import { memo } from 'react'
import { useShallow } from 'zustand/react/shallow'

import type { FineTuneRun, FineTuneStage } from '@/api/endpoints/fine-tuning'
import { ACTIVE_STAGES } from '@/api/endpoints/fine-tuning'
import { EmptyState } from '@/components/ui/empty-state'
import { Pagination } from '@/components/ui/pagination'
import { StatusBadge } from '@/components/ui/status-badge'
import { useListPagination } from '@/hooks/use-list-pagination'
import { useFineTuningStore } from '@/stores/fine-tuning'
import { formatDateTime } from '@/utils/format'

const STAGE_STATUS_MAP: Record<FineTuneStage, 'active' | 'idle' | 'error' | 'offline'> = {
  complete: 'active',
  failed: 'error',
  idle: 'idle',
  generating_data: 'active',
  mining_negatives: 'active',
  training: 'active',
  evaluating: 'active',
  deploying: 'active',
}

/**
 * Count DISTINCT completed stages that belong to the active-pipeline set.
 * Deduplicating guards against a repeated stage name in ``stages_completed``
 * inflating the count beyond ``ACTIVE_STAGES.size`` (e.g. rendering "9/7").
 */
function countActiveStagesCompleted(stages: readonly string[]): number {
  const unique = new Set<FineTuneStage>()
  for (const s of stages) {
    if (ACTIVE_STAGES.has(s as FineTuneStage)) {
      unique.add(s as FineTuneStage)
    }
  }
  return unique.size
}

// Memoised so a single run's WS-driven progress update does not re-render
// every other row in the table.
const RunRow = memo(function RunRow({ run }: { run: FineTuneRun }) {
  return (
    <tr>
      <td className="py-2 pr-4 font-mono text-xs">
        {formatDateTime(run.started_at)}
      </td>
      <td className="py-2 pr-4 font-mono text-xs">
        {run.duration_seconds != null
          ? formatDuration(run.duration_seconds)
          : '--'}
      </td>
      <td className="py-2 pr-4">
        <span className="inline-flex items-center gap-1.5">
          <StatusBadge
            status={STAGE_STATUS_MAP[run.stage]}
          />
          <span className="text-xs">{formatStage(run.stage)}</span>
        </span>
      </td>
      <td className="py-2 pr-4 text-xs text-muted-foreground">
        {countActiveStagesCompleted(run.stages_completed)}/{ACTIVE_STAGES.size}
      </td>
      <td className="py-2 font-mono text-xs text-muted-foreground">
        {run.config.source_dir}
      </td>
    </tr>
  )
})

export function RunHistoryTable() {
  const { runs } = useFineTuningStore(useShallow((s) => ({ runs: s.runs })))
  const pagination = useListPagination({ items: runs, namespace: 'fine-tuning-runs' })

  if (runs.length === 0) {
    return (
      <EmptyState
        title="No runs yet"
        description="Start your first fine-tuning run to see history here."
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="pb-2 pr-4">Date</th>
              <th className="pb-2 pr-4">Duration</th>
              <th className="pb-2 pr-4">Status</th>
              <th className="pb-2 pr-4">Stages</th>
              <th className="pb-2">Source</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {pagination.paginatedItems.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={pagination.page}
        pageSize={pagination.pageSize}
        total={pagination.totalItems}
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

function formatDuration(raw: number): string {
  const seconds = Math.max(0, Math.round(raw))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

const STAGE_LABELS: Record<FineTuneStage, string> = {
  complete: 'Completed',
  failed: 'Failed',
  idle: 'Idle',
  generating_data: 'Generating',
  mining_negatives: 'Mining',
  training: 'Training',
  evaluating: 'Evaluating',
  deploying: 'Deploying',
}

function formatStage(stage: FineTuneStage): string {
  return STAGE_LABELS[stage]
}
