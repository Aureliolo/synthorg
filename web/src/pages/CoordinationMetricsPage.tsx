/**
 * Coordination metrics analytics.
 *
 * Per-run coordination metrics from completed multi-agent runs, queried
 * from ``GET /coordination/metrics``. Each record captures the Kim et al.
 * coordination metrics (efficiency, overhead, redundancy, error
 * amplification) for one task/lead-agent run, filterable by task and
 * agent.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Network } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonTable } from '@/components/ui/skeleton'
import { listCoordinationMetrics } from '@/api/endpoints/coordination'
import type { CoordinationMetricsRecord } from '@/api/types'
import { useListPagination } from '@/hooks/use-list-pagination'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { formatDateTime, formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('CoordinationMetricsPage')

/** Render a nullable metric value, falling back to an em-dash placeholder. */
function metricValue(value: number | null | undefined): string {
  return value == null ? '--' : formatNumber(value)
}

interface CoordinationMetricsData {
  records: readonly CoordinationMetricsRecord[]
  loading: boolean
  error: string | null
  taskId: string
  agentId: string
  setTaskId: (value: string) => void
  setAgentId: (value: string) => void
  refresh: () => void
}

/** Case-insensitive substring match, treating a blank needle as "match all". */
function matchesFilter(haystack: string | null, needle: string): boolean {
  const trimmed = needle.trim().toLowerCase()
  if (trimmed === '') return true
  return (haystack ?? '').toLowerCase().includes(trimmed)
}

function useCoordinationMetricsData(): CoordinationMetricsData {
  const [allRecords, setAllRecords] = useState<readonly CoordinationMetricsRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('')
  const [agentId, setAgentId] = useState('')

  // Fetch the full snapshot once; task / agent filtering is applied
  // client-side so typing in the filters never refetches or races.
  const fetchMetrics = useCallback(() => {
    setLoading(true)
    setError(null)
    void listCoordinationMetrics()
      .then((result) => setAllRecords(result))
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('listCoordinationMetrics failed', { error: sanitizeForLog(message) })
        setError(message)
      })
      .finally(() => setLoading(false))
  }, [])

  // Defer the initial fetch to a microtask so the effect body itself
  // performs no synchronous setState (set-state-in-effect rule); the
  // loading/error writes happen inside the deferred fetchMetrics call.
  useEffect(() => {
    void Promise.resolve().then(fetchMetrics)
  }, [fetchMetrics])

  const records = useMemo(
    () =>
      allRecords.filter(
        (r) => matchesFilter(r.task_id, taskId) && matchesFilter(r.agent_id, agentId),
      ),
    [allRecords, taskId, agentId],
  )

  return {
    records, loading, error, taskId, agentId, setTaskId, setAgentId, refresh: fetchMetrics,
  }
}

function CoordinationMetricsRow({ record }: { record: CoordinationMetricsRecord }) {
  const { metrics } = record
  return (
    <tr className="border-t border-border">
      <td className="py-2 pr-4 font-mono text-xs text-foreground">{record.task_id}</td>
      <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
        {record.agent_id ?? 'system'}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">{record.team_size}</td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {metricValue(metrics.efficiency?.value)}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {metrics.overhead == null ? '--' : `${metrics.overhead.value_percent.toFixed(1)}%`}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {metricValue(metrics.redundancy_rate?.value)}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {metricValue(metrics.error_amplification?.value)}
      </td>
      <td className="py-2 text-xs text-muted-foreground">{formatDateTime(record.computed_at)}</td>
    </tr>
  )
}

function CoordinationMetricsTable({
  records,
}: {
  records: readonly CoordinationMetricsRecord[]
}) {
  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize } =
    useListPagination({ items: records, namespace: 'coord' })
  return (
    <div className="space-y-section-gap">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[44rem] text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="py-2 pr-4 font-medium">Task</th>
              <th className="py-2 pr-4 font-medium">Lead agent</th>
              <th className="py-2 pr-4 text-right font-medium">Team</th>
              <th className="py-2 pr-4 text-right font-medium">Efficiency</th>
              <th className="py-2 pr-4 text-right font-medium">Overhead</th>
              <th className="py-2 pr-4 text-right font-medium">Redundancy</th>
              <th className="py-2 pr-4 text-right font-medium">Error amp.</th>
              <th className="py-2 font-medium">Computed</th>
            </tr>
          </thead>
          <tbody>
            {paginatedItems.map((record) => (
              <CoordinationMetricsRow
                key={`${record.task_id}-${record.agent_id ?? 'system'}-${record.computed_at}`}
                record={record}
              />
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />
    </div>
  )
}

export default function CoordinationMetricsPage() {
  const { records, loading, error, taskId, agentId, setTaskId, setAgentId, refresh } =
    useCoordinationMetricsData()

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Coordination metrics" count={records.length} refreshing={loading} />

      <div className="flex flex-wrap gap-grid-gap">
        <SearchInput
          value={taskId}
          onChange={setTaskId}
          placeholder="Filter by task ID"
          ariaLabel="Filter coordination metrics by task ID"
        />
        <SearchInput
          value={agentId}
          onChange={setAgentId}
          placeholder="Filter by agent ID"
          ariaLabel="Filter coordination metrics by agent ID"
        />
      </div>

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load coordination metrics"
          description={error}
          onRetry={refresh}
        />
      )}

      <ErrorBoundary level="section">
        <SectionCard title="Coordination runs" icon={Network}>
          {loading && records.length === 0 ? (
            <SkeletonTable rows={5} columns={8} />
          ) : records.length === 0 ? (
            <EmptyState
              icon={Network}
              title="No coordination metrics yet"
              description="Metrics appear here after multi-agent runs complete. Adjust the filters or run a coordinated task."
            />
          ) : (
            <CoordinationMetricsTable records={records} />
          )}
        </SectionCard>
      </ErrorBoundary>
    </div>
  )
}
