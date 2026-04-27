import { GraduationCap, Users } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { listAgents } from '@/api/endpoints/agents'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonTable } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { createLogger } from '@/lib/logger'
import { useTrainingStore } from '@/stores/training'
import type { AgentConfig } from '@/api/types/agents'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import {
  TrainingPlanTable,
  type TrainingPlanRow,
} from './training/TrainingPlanTable'

const log = createLogger('training-page')

export default function TrainingPage() {
  const [agents, setAgents] = useState<readonly AgentConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const plansByAgent = useTrainingStore((s) => s.plansByAgent)
  const resultsByAgent = useTrainingStore((s) => s.resultsByAgent)
  const hydrateForAgent = useTrainingStore((s) => s.hydrateForAgent)
  const executePlan = useTrainingStore((s) => s.executePlan)

  useEffect(() => {
    let cancelled = false
    // Kick off the fetch in a microtask so the initial render completes
    // first (avoids the synchronous set-state-in-effect lint rule).
    // Ask for the full roster up-front so the table does not silently
    // truncate to the default 50-agent page.
    void Promise.resolve()
      .then(() => listAgents({ limit: 200 }))
      .then((paginated) => {
        if (!cancelled) {
          setAgents(paginated.data)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        log.error(
          'Failed to load agents',
          sanitizeForLog({ err, message: getErrorMessage(err) }),
        )
        if (!cancelled) {
          setError(getErrorMessage(err))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    // Hydrate plan + result for each agent in bounded batches so a
    // large roster does not fan out 200 concurrent requests at once.
    // Best-effort: missing rows surface as "no plan" instead of errors
    // (the store swallows 404).
    const BATCH_SIZE = 10
    let cancelled = false
    void (async () => {
      for (let i = 0; i < agents.length; i += BATCH_SIZE) {
        if (cancelled) return
        const batch = agents.slice(i, i + BATCH_SIZE)
        await Promise.all(batch.map((agent) => hydrateForAgent(agent.name)))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [agents, hydrateForAgent])

  const rows: readonly TrainingPlanRow[] = useMemo(
    () =>
      agents.map((agent) => ({
        agentName: agent.name,
        plan: plansByAgent[agent.name] ?? null,
        result: resultsByAgent[agent.name] ?? null,
      })),
    [agents, plansByAgent, resultsByAgent],
  )

  const handleExecute = useCallback(
    (agentName: string) => {
      void executePlan(agentName)
    },
    [executePlan],
  )

  const metrics = useMemo(() => {
    const totalPlans = rows.filter((r) => r.plan !== null).length
    const pending = rows.filter((r) => r.plan?.status === 'pending').length
    const executed = rows.filter((r) => r.plan?.status === 'executed').length
    const totalItems = rows.reduce(
      (sum, r) =>
        sum + (r.result?.items_stored.reduce((s, [, c]) => s + c, 0) ?? 0),
      0,
    )
    return { totalPlans, pending, executed, totalItems }
  }, [rows])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Training" count={rows.length} countLabel={`${rows.length} agents`} />

      {error && (
        <ErrorBanner severity="error" title="Could not load training plans" description={error} />
      )}

      <StaggerGroup className="grid grid-cols-2 gap-grid-gap lg:grid-cols-4">
        <StaggerItem>
          <MetricCard label="TOTAL PLANS" value={metrics.totalPlans} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="PENDING" value={metrics.pending} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="EXECUTED" value={metrics.executed} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="ITEMS STORED" value={metrics.totalItems} />
        </StaggerItem>
      </StaggerGroup>

      <SectionCard title="Agent training plans" icon={GraduationCap}>
        {loading ? (
          <SkeletonTable rows={6} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No agents to train"
            description="Agents appear here once the company has been set up. Run the setup wizard to bring a roster online."
          />
        ) : (
          <TrainingPlanTable rows={rows} onExecute={handleExecute} />
        )}
      </SectionCard>
    </div>
  )
}
