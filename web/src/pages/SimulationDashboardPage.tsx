import { useCallback, useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'

import {
  cancelSimulation,
  getSimulationReport,
  listSimulations,
  type SimulationReport,
  type SimulationStatus,
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

/**
 * Simulation run overview.
 *
 * Aggregates metrics across every known simulation record so
 * operators get a single-glance view of throughput and
 * acceptance rates. Surfaces cancel and summary-report actions
 * per run.
 */
export default function SimulationDashboardPage() {
  const { capabilities, loading: capLoading } = useCapabilities()
  const [runs, setRuns] = useState<readonly SimulationStatus[]>([])
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
  // simulations subsystem is not configured for this deployment. The
  // backend route is also not registered (returns 404), so calling
  // listSimulations() would log a 404 in the audit trail per the
  // issue #1666 B-3 contract. The early-return path that renders the
  // EmptyState is below all hooks so React's hook-order rules stay
  // satisfied across renders. While ``capLoading`` is true the
  // skeleton branch below covers the in-flight window so the page
  // never flashes "No simulation runs yet" against an unconfigured
  // deployment.
  useEffect(() => {
    if (capLoading) {
      return
    }
    if (!capabilities.simulations) {
      // Defer the loading flip out of the same synchronous render
      // frame so eslint-react's set-state-in-effect rule stays
      // satisfied and React batches one render instead of two.
      queueMicrotask(() => setLoading(false))
      return
    }
    void refresh()
  }, [refresh, capLoading, capabilities.simulations])

  if (!capLoading && !capabilities.simulations) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Simulations" />
        <EmptyState
          icon={Activity}
          title="Simulations not configured"
          description={
            'This deployment did not enable the client simulation ' +
            'runtime. Configure it in your backend setup to start ' +
            'tracking simulation runs.'
          }
        />
      </div>
    )
  }

  // Hold the skeleton until capabilities resolve AND the first refresh
  // either lands data or sets loading=false. This prevents a one-frame
  // "No simulation runs yet" flash on an unconfigured-but-not-yet-
  // resolved deployment.
  if (capLoading || (loading && runs.length === 0)) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Simulations" />
        <SkeletonCard />
      </div>
    )
  }

  const totalTasksCreated = runs.reduce(
    (sum, run) => sum + run.metrics.total_tasks_created,
    0,
  )
  const totalAccepted = runs.reduce(
    (sum, run) => sum + run.metrics.tasks_accepted,
    0,
  )
  const totalRejected = runs.reduce(
    (sum, run) => sum + run.metrics.tasks_rejected,
    0,
  )
  const runningCount = runs.filter((run) => run.status === 'running').length

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Simulations" count={runs.length} countLabel={`${runs.length} runs`} />

      {error && (
        <ErrorBanner severity="error" title="Simulation error" description={error} />
      )}

      <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-4">
        <MetricCard label="Active runs" value={runningCount.toString()} />
        <MetricCard
          label="Tasks created"
          value={totalTasksCreated.toString()}
        />
        <MetricCard label="Accepted" value={totalAccepted.toString()} />
        <MetricCard label="Rejected" value={totalRejected.toString()} />
      </div>

      {runs.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No simulation runs yet"
          description="Start a simulation via POST /simulations to populate this dashboard."
        />
      ) : (
        <SectionCard title="Recent runs" icon={Activity}>
          <ul className="space-y-2">
            {runs.map((run) => {
              const terminal = ['completed', 'cancelled', 'failed'].includes(
                run.status,
              )
              return (
                <li
                  key={run.simulation_id}
                  className="space-y-2 rounded-md border border-border bg-card-hover p-card text-sm"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-medium text-foreground">
                        {run.simulation_id}
                      </div>
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
                  {run.status === 'failed' && run.error && (
                    <p className="text-xs text-danger">{run.error}</p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void handleShowReport(run.simulation_id)}
                    >
                      Report
                    </Button>
                    {!terminal && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void handleCancel(run.simulation_id)}
                      >
                        Cancel
                      </Button>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </SectionCard>
      )}

      {report && (
        <SectionCard
          title={`Report: ${report.simulation_id}`}
          icon={Activity}
        >
          <pre className="overflow-auto rounded-md border border-border bg-card-hover p-card text-xs text-foreground">
            {JSON.stringify(report, null, 2)}
          </pre>
        </SectionCard>
      )}
    </div>
  )
}
