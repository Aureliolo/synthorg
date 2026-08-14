import { useCallback, useEffect, useRef, useState } from 'react'

import {
  getCompletionOracleReports,
  getCompletionOracleSummary,
  getRedTeamReports,
  getRedTeamSummary,
} from '@/api/endpoints/gate-verdicts'
import type { RedTeamReportRecord, RedTeamVerdict } from '@/api/types/cockpit'
import type {
  CompletionOracleReportRecord,
  CompletionOracleVerdict,
  GateVerdictSummary,
} from '@/api/types/gate-verdicts'
import { createLogger } from '@/lib/logger'

const log = createLogger('useGateVerdicts')

const RECENT_LIMIT = 10

/** Which gate an agent judges for, derived from the role it holds. */
export type GateKind = 'completion_oracle' | 'red_team'

/** The fields both archives carry, flattened to what the panel renders. */
interface GateVerdictRowBase {
  readonly key: string
  readonly executionId: string
  readonly taskId: string
  readonly summary: string
  readonly recordedAt: string
  readonly provider: string | null
  readonly modelId: string | null
  readonly capability: string | null
}

/**
 * One archived verdict, discriminated on the gate that reached it.
 *
 * The two verdict vocabularies do not overlap, so the gate tag is what lets
 * the badge narrow to its own union instead of being handed a bare string.
 */
export type GateVerdictRow =
  | (GateVerdictRowBase & {
      readonly gate: 'completion_oracle'
      readonly verdict: CompletionOracleVerdict
    })
  | (GateVerdictRowBase & {
      readonly gate: 'red_team'
      readonly verdict: RedTeamVerdict
    })

export interface GateVerdictsController {
  readonly gate: GateKind
  readonly summary: GateVerdictSummary | null
  readonly recent: readonly GateVerdictRow[]
  readonly loading: boolean
  readonly loadError: boolean
  readonly refetch: () => Promise<void>
}

/** Map an agent's role name onto the gate it judges for, or null for neither. */
export function gateForRole(role: string): GateKind | null {
  const normalised = role.trim().toLowerCase()
  if (normalised === 'completion reviewer') return 'completion_oracle'
  if (normalised === 'red team') return 'red_team'
  return null
}

function oracleRow(record: CompletionOracleReportRecord, index: number): GateVerdictRow {
  return {
    gate: 'completion_oracle',
    key: `${record.execution_id}-${String(index)}`,
    executionId: record.execution_id,
    taskId: record.task_id,
    verdict: record.verdict,
    summary: record.report.summary,
    recordedAt: record.recorded_at,
    provider: record.reviewer_provider ?? null,
    modelId: record.reviewer_model_id ?? null,
    capability: record.reviewer_capability ?? null,
  }
}

function redTeamRow(record: RedTeamReportRecord, index: number): GateVerdictRow {
  return {
    gate: 'red_team',
    key: `${record.execution_id}-${String(index)}`,
    executionId: record.execution_id,
    taskId: record.task_id,
    verdict: record.verdict,
    summary: record.report.summary,
    recordedAt: record.recorded_at,
    provider: record.red_team_provider ?? null,
    modelId: record.red_team_model_id ?? null,
    capability: record.red_team_capability ?? null,
  }
}

async function fetchOracle(
  agentId: string,
): Promise<{ summary: GateVerdictSummary; recent: readonly GateVerdictRow[] }> {
  const [summary, page] = await Promise.all([
    getCompletionOracleSummary({ reviewer_agent_id: agentId }),
    getCompletionOracleReports({ reviewer_agent_id: agentId, limit: RECENT_LIMIT }),
  ])
  return { summary, recent: page.data.map(oracleRow) }
}

async function fetchRedTeam(
  agentId: string,
): Promise<{ summary: GateVerdictSummary; recent: readonly GateVerdictRow[] }> {
  const [summary, page] = await Promise.all([
    getRedTeamSummary({ red_team_agent_id: agentId }),
    getRedTeamReports({ red_team_agent_id: agentId, limit: RECENT_LIMIT }),
  ])
  return { summary, recent: page.data.map(redTeamRow) }
}

/**
 * Load one gate-role agent's archived verdicts.
 *
 * Pure API consumer: every field comes from the two archive endpoints on
 * mount, nothing is cached client-side, and the counts are the backend's
 * own totals rather than a tally of the page on screen.
 */
export function useGateVerdicts(agentId: string, gate: GateKind): GateVerdictsController {
  const [summary, setSummary] = useState<GateVerdictSummary | null>(null)
  const [recent, setRecent] = useState<readonly GateVerdictRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  // Guard against a slow response for a previous agent landing after the
  // panel switched agents.
  const activeRef = useRef(`${agentId}:${gate}`)
  activeRef.current = `${agentId}:${gate}`

  const refetch = useCallback(async () => {
    const requested = `${agentId}:${gate}`
    setLoading(true)
    setLoadError(false)
    try {
      const result =
        gate === 'completion_oracle'
          ? await fetchOracle(agentId)
          : await fetchRedTeam(agentId)
      if (activeRef.current !== requested) return
      setSummary(result.summary)
      setRecent(result.recent)
    } catch (err) {
      if (activeRef.current !== requested) return
      log.warn('failed to load gate verdicts', err)
      setLoadError(true)
      setSummary(null)
      setRecent([])
    } finally {
      if (activeRef.current === requested) setLoading(false)
    }
  }, [agentId, gate])

  useEffect(() => {
    void refetch()
  }, [refetch])

  return { gate, summary, recent, loading, loadError, refetch }
}
