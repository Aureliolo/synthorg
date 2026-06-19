import { useCallback, useEffect, useMemo, useState } from 'react'

import { listAgents } from '@/api/endpoints/agents'
import type { AgentConfig } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useTrainingStore } from '@/stores/training'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createCancellationToken } from '@/utils/cancellation'

import type { TrainingPlanRow } from './TrainingPlanTable'

const log = createLogger('training-page')

const HYDRATE_BATCH_SIZE = 10

export interface TrainingPageController {
  loading: boolean
  error: string | null
  rows: readonly TrainingPlanRow[]
  metrics: {
    totalPlans: number
    pending: number
    executed: number
    totalItems: number
  }
  handleExecute: (agentId: string) => void
}

export function useTrainingPageController(): TrainingPageController {
  const [agents, setAgents] = useState<readonly AgentConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const plansByAgent = useTrainingStore((s) => s.plansByAgent)
  const resultsByAgent = useTrainingStore((s) => s.resultsByAgent)
  const hydrateForAgent = useTrainingStore((s) => s.hydrateForAgent)
  const executePlan = useTrainingStore((s) => s.executePlan)

  useEffect(() => loadAgentRoster(setAgents, setLoading, setError), [])

  useEffect(
    () => hydrateAgentsInBatches(agents, hydrateForAgent),
    [agents, hydrateForAgent],
  )

  const rows = useMemo<readonly TrainingPlanRow[]>(
    () =>
      agents.map((agent) => ({
        agentId: agent.id,
        agentName: agent.name,
        plan: plansByAgent[agent.id] ?? null,
        result: resultsByAgent[agent.id] ?? null,
      })),
    [agents, plansByAgent, resultsByAgent],
  )

  const handleExecute = useCallback(
    (agentId: string) => void executePlan(agentId),
    [executePlan],
  )

  const metrics = useMemo(() => computeMetrics(rows), [rows])

  return { loading, error, rows, metrics, handleExecute }
}

function loadAgentRoster(
  setAgents: (agents: readonly AgentConfig[]) => void,
  setLoading: (loading: boolean) => void,
  setError: (error: string | null) => void,
): () => void {
  const token = createCancellationToken()
  // Kick off the fetch in a microtask so the initial render completes first
  // (avoids the synchronous set-state-in-effect lint rule). Ask for the full
  // roster up-front so the table does not silently truncate to the default
  // 50-agent page.
  void Promise.resolve()
    .then(() => listAgents({ limit: 200 }))
    .then((paginated) => {
      if (!token.cancelled()) {
        setAgents(paginated.data)
        setLoading(false)
      }
    })
    .catch((err: unknown) => {
      logRosterError(err)
      if (!token.cancelled()) {
        setError(getErrorMessage(err))
        setLoading(false)
      }
    })
  return () => {
    token.cancel()
  }
}

function logRosterError(err: unknown): void {
  // Sanitize dynamic error fields before they reach the log pipeline; an
  // attacker-influenced message must not carry control bytes into the sink.
  // Pass the structured fields the operator actually needs to diagnose:
  // status code (404 vs 5xx vs network), error class name, and a
  // length-bounded message.
  log.error('Failed to load agents', {
    errorType: sanitizeForLog(
      err instanceof Error ? err.constructor.name : typeof err,
    ),
    statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
    error: sanitizeForLog(getErrorMessage(err)),
  })
}

function hydrateAgentsInBatches(
  agents: readonly AgentConfig[],
  hydrateForAgent: (agentId: string) => Promise<unknown>,
): () => void {
  // Hydrate plan + result for each agent in bounded batches so a large
  // roster does not fan out 200 concurrent requests at once. Best-effort:
  // missing rows surface as "no plan" instead of errors (the store
  // swallows 404).
  const token = createCancellationToken()
  void (async () => {
    for (let i = 0; i < agents.length; i += HYDRATE_BATCH_SIZE) {
      if (token.cancelled()) return
      const batch = agents.slice(i, i + HYDRATE_BATCH_SIZE)
      await Promise.all(batch.map((agent) => hydrateForAgent(agent.id)))
    }
  })()
  return () => {
    token.cancel()
  }
}

function computeMetrics(rows: readonly TrainingPlanRow[]) {
  const totalPlans = rows.filter((r) => r.plan !== null).length
  const pending = rows.filter((r) => r.plan?.status === 'pending').length
  const executed = rows.filter((r) => r.plan?.status === 'executed').length
  const totalItems = rows.reduce(
    (sum, r) =>
      sum + (r.result?.items_stored.reduce((s, [, c]) => s + c, 0) ?? 0),
    0,
  )
  return { totalPlans, pending, executed, totalItems }
}
