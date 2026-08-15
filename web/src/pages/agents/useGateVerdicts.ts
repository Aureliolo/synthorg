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
  /** Whether a first answer has arrived, so a retry keeps the card mounted. */
  readonly settledOnce: boolean
}

/**
 * The two gate roles, spelled as `core/role_catalog.py` spells them.
 *
 * The backend folds a role with `str.casefold()`; JavaScript has no casefold,
 * so this uses `toLowerCase()`. The two differ only on characters neither of
 * these names contains (sharp-s, Turkish dotted-I, final sigma), so for these
 * two the folds agree, and a role that is not one of them lands on `null`
 * either way.
 */
const GATE_ROLE_NAMES: Readonly<Record<string, GateKind>> = {
  'completion reviewer': 'completion_oracle',
  'red team': 'red_team',
}

/** Map an agent's role name onto the gate it judges for, or null for neither. */
export function gateForRole(role: string): GateKind | null {
  return GATE_ROLE_NAMES[role.trim().toLowerCase()] ?? null
}

/**
 * Row key: the archive's own key, with a composite fallback.
 *
 * An execution can hold several verdicts (a task decided, re-opened and
 * decided again), so `report_id` is the only field that identifies a row.
 * It is optional on the DTO, and `String(null)` is the literal `"null"`:
 * two such rows would share one key and break list reconciliation.
 */
function rowKey(reportId: number | null | undefined, record: { execution_id: string; recorded_at: string }): string {
  return reportId == null ? `${record.execution_id}:${record.recorded_at}` : String(reportId)
}

function oracleRow(record: CompletionOracleReportRecord): GateVerdictRow {
  return {
    gate: 'completion_oracle',
    key: rowKey(record.report_id, record),
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

function redTeamRow(record: RedTeamReportRecord): GateVerdictRow {
  return {
    gate: 'red_team',
    key: rowKey(record.report_id, record),
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
  // Distinguishes the first load from a retry. The panel renders nothing
  // until the first answer arrives (there is no card to show yet), but a
  // retry must keep the card up: it holds the Retry button the operator
  // just pressed, and unmounting it makes the control vanish under them.
  const [settledOnce, setSettledOnce] = useState(false)

  // Which subject the state on hand describes. Both are props, so they change
  // a full render before the request for the new subject resolves, and without
  // this the panel renders one agent's verdicts under another agent's heading.
  // Adjusted during render rather than in an effect so no commit ever paints
  // the mismatch.
  const subject = `${agentId}:${gate}`
  const [renderedSubject, setRenderedSubject] = useState(subject)
  if (renderedSubject !== subject) {
    setRenderedSubject(subject)
    setSummary(null)
    setRecent([])
    setLoading(true)
    setLoadError(false)
    setSettledOnce(false)
  }

  // Which request is current. A counter rather than the agent + gate pair:
  // two Retry clicks for the SAME agent are two requests, and identifying
  // them by their subject cannot tell the slower one to stand down. Written
  // only inside the callback, so a render never mutates it.
  const requestRef = useRef(0)

  const refetch = useCallback(async () => {
    const requested = (requestRef.current += 1)
    setLoading(true)
    setLoadError(false)
    try {
      const result =
        gate === 'completion_oracle'
          ? await fetchOracle(agentId)
          : await fetchRedTeam(agentId)
      if (requestRef.current !== requested) return
      setSummary(result.summary)
      setRecent(result.recent)
    } catch (err) {
      if (requestRef.current !== requested) return
      log.warn('failed to load gate verdicts', err)
      setLoadError(true)
      setSummary(null)
      setRecent([])
    } finally {
      if (requestRef.current === requested) {
        setLoading(false)
        setSettledOnce(true)
      }
    }
  }, [agentId, gate])

  useEffect(() => {
    void refetch()
  }, [refetch])

  return { gate, summary, recent, loading, loadError, refetch, settledOnce }
}
