import { useCallback, useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'

import {
  cancelSimulation,
  getSimulationReport,
  listSimulations,
  type SimulationReport,
  type SimulationStatusResponse,
} from '@/api/endpoints/clients'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useCapabilities } from '@/hooks/useCapabilities'
import { createLogger } from '@/lib/logger'

const log = createLogger('SimulationDashboardPage')

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['completed', 'cancelled', 'failed'])

interface SimulationDashboardState {
  capabilities: ReturnType<typeof useCapabilities>['capabilities']
  capLoading: boolean
  capError: string | null
  runs: readonly SimulationStatusResponse[]
  loading: boolean
  error: string | null
  report: SimulationReport | null
  handleCancel: (simulationId: string) => Promise<void>
  handleShowReport: (simulationId: string) => Promise<void>
}

function useSimulationDashboard(): SimulationDashboardState {
  const { capabilities, loading: capLoading, error: capError } = useCapabilities()
  const [runs, setRuns] = useState<readonly SimulationStatusResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<SimulationReport | null>(null)

  const refresh = useCallback(async () => {
    try {
      const result = await listSimulations({ limit: 100 })
      setRuns(result.data)
      setError(null)
    } catch (err) {
      log.error('list_simulations_failed', err)
      setError('Failed to load simulation runs.')
    } finally {
      setLoading(false)
    }
  }, [])

  const handleCancel = useCallback(
    async (simulationId: string) => {
      try {
        await cancelSimulation(simulationId)
        await refresh()
      } catch (err) {
        log.error('cancel_simulation_failed', err)
        setError('Failed to cancel simulation.')
      }
    },
    [refresh],
  )

  const handleShowReport = useCallback(async (simulationId: string) => {
    setReport(null)
    try {
      const fetched = await getSimulationReport(simulationId, 'summary')
      setReport(fetched)
      setError(null)
    } catch (err) {
      log.error('get_simulation_report_failed', err)
      setError('Failed to load simulation report.')
    }
  }, [])

  // Capability-gated effect: skip the network call entirely when the
  // simulations subsystem is not configured. The backend route is also
  // unregistered (404), so calling listSimulations() would log a 404 in
  // the audit trail per the issue #1666 B-3 contract.
  useEffect(() => {
    if (capLoading) return
    if (!capabilities.simulations) {
      // Defer the loading flip out of the same synchronous render frame
      // so eslint-react's set-state-in-effect rule stays satisfied.
      queueMicrotask(() => setLoading(false))
      return
    }
    void refresh()
  }, [refresh, capLoading, capabilities.simulations])

  return {
    capabilities, capLoading, capError, runs, loading, error, report, handleCancel, handleShowReport,
  }
}

type SimulationFallback = 'cap-error' | 'not-configured' | 'loading' | null

function simulationFallback(
  capLoading: boolean,
  capError: string | null,
  simulationsEnabled: boolean,
  loading: boolean,
  runsLength: number,
): SimulationFallback {
  if (!capLoading && capError !== null) return 'cap-error'
  if (!capLoading && !simulationsEnabled) return 'not-configured'
  // Hold the skeleton until capabilities resolve AND the first refresh
  // either lands data or sets loading=false, preventing a one-frame
  // "No simulation runs yet" flash on an unconfigured deployment.
  if (capLoading || (loading && runsLength === 0)) return 'loading'
  return null
}

function SimulationFallbackView({
  state,
  capError,
}: {
  state: Exclude<SimulationFallback, null>
  capError: string | null
}) {
  if (state === 'cap-error') {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Simulations" />
        <ErrorBanner
          severity="error"
          title="Could not determine available features"
          description={capError ?? undefined}
        />
      </div>
    )
  }
  if (state === 'not-configured') {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Simulations" />
        <EmptyState
          icon={Activity}
          title="Simulations not configured"
          description={
            'This deployment did not enable the client simulation runtime. Configure it in ' +
            'your backend setup to start tracking simulation runs.'
          }
        />
      </div>
    )
  }
  return (
    <div className="space-y-section-gap">
      <ListHeader title="Simulations" />
      <SkeletonCard />
    </div>
  )
}

function SimulationMetrics({ runs }: { runs: readonly SimulationStatusResponse[] }) {
  const totalTasksCreated = runs.reduce((sum, run) => sum + run.metrics.total_tasks_created, 0)
  const totalAccepted = runs.reduce((sum, run) => sum + run.metrics.tasks_accepted, 0)
  const totalRejected = runs.reduce((sum, run) => sum + run.metrics.tasks_rejected, 0)
  const runningCount = runs.filter((run) => run.status === 'running').length
  return (
    <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-4">
      <MetricCard label="Active runs" value={runningCount.toString()} />
      <MetricCard label="Tasks created" value={totalTasksCreated.toString()} />
      <MetricCard label="Accepted" value={totalAccepted.toString()} />
      <MetricCard label="Rejected" value={totalRejected.toString()} />
    </div>
  )
}

function SimulationRunItem({
  run,
  onShowReport,
  onCancel,
}: {
  run: SimulationStatusResponse
  onShowReport: (id: string) => void
  onCancel: (id: string) => void
}) {
  const terminal = TERMINAL_STATUSES.has(run.status)
  return (
    <li className="space-y-2 rounded-md border border-border bg-card-hover p-card text-sm">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-medium text-foreground">{run.simulation_id}</div>
          <div className="text-xs text-text-secondary">
            {run.config.project_id} · {run.config.rounds} round(s)
          </div>
        </div>
        <span
          className="rounded-full border border-border px-2 py-1 text-xs text-foreground"
          aria-label={`Status: ${run.status}`}
        >
          {run.status}
        </span>
      </div>
      {run.status === 'failed' && run.error && <p className="text-xs text-danger">{run.error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onShowReport(run.simulation_id)}>
          Report
        </Button>
        {!terminal && (
          <Button size="sm" variant="outline" onClick={() => onCancel(run.simulation_id)}>
            Cancel
          </Button>
        )}
      </div>
    </li>
  )
}

function SimulationRunsList({
  runs,
  onShowReport,
  onCancel,
}: {
  runs: readonly SimulationStatusResponse[]
  onShowReport: (id: string) => void
  onCancel: (id: string) => void
}) {
  if (runs.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No simulation runs yet"
        description="Start a simulation via POST /simulations to populate this dashboard."
      />
    )
  }
  return (
    <SectionCard title="Recent runs" icon={Activity}>
      <ul className="space-y-2">
        {runs.map((run) => (
          <SimulationRunItem
            key={run.simulation_id}
            run={run}
            onShowReport={onShowReport}
            onCancel={onCancel}
          />
        ))}
      </ul>
    </SectionCard>
  )
}

/**
 * Simulation run overview.
 *
 * Aggregates metrics across every known simulation record so operators
 * get a single-glance view of throughput and acceptance rates. Surfaces
 * cancel and summary-report actions per run.
 */
export default function SimulationDashboardPage() {
  const s = useSimulationDashboard()
  const fallback = simulationFallback(
    s.capLoading,
    s.capError,
    s.capabilities.simulations,
    s.loading,
    s.runs.length,
  )

  if (fallback) {
    return <SimulationFallbackView state={fallback} capError={s.capError} />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Simulations" count={s.runs.length} countLabel={`${s.runs.length} runs`} />

      {s.error && (
        <ErrorBanner severity="error" title="Simulation error" description={s.error} />
      )}

      <SimulationMetrics runs={s.runs} />

      <SimulationRunsList
        runs={s.runs}
        onShowReport={(id) => void s.handleShowReport(id)}
        onCancel={(id) => void s.handleCancel(id)}
      />

      {s.report && (
        <SectionCard title={`Report: ${s.report.simulation_id}`} icon={Activity}>
          <pre className="overflow-auto rounded-md border border-border bg-card-hover p-card text-xs text-foreground">
            {JSON.stringify(s.report, null, 2)}
          </pre>
        </SectionCard>
      )}
    </div>
  )
}
