/**
 * Admin audit-log viewer.
 *
 * Consumes GET /security/audit and surfaces the security evaluation
 * trail with the most useful filters (action type, verdict, risk
 * level, agent / tool substring, time window) and cursor pagination.
 * Built for admin operators who need to investigate "why was tool X
 * denied for agent Y" without reaching for the database.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Shield } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { listAuditEntries } from '@/api/endpoints/audit'
import type { AuditEntry } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'

const log = createLogger('AdminAuditLogPage')

const DEFAULT_PAGE_SIZE = 50

// Derived from the generated ``AuditEntry.verdict`` enum so a new
// backend verdict surfaces as a type error here instead of silently
// breaking the filter. The empty string is the "any verdict"
// sentinel used by the SelectField. The VERDICT_OPTIONS literal
// below stays explicit on purpose: option labels are user-visible
// copy, not something to auto-derive from the schema.
type VerdictFilter = '' | AuditEntry['verdict']

const VERDICT_OPTIONS: ReadonlyArray<{ value: VerdictFilter; label: string }> = [
  { value: '', label: 'Any verdict' },
  { value: 'allow', label: 'Allow' },
  { value: 'deny', label: 'Deny' },
  { value: 'escalate', label: 'Escalate' },
  { value: 'output_scan', label: 'Output scan' },
]

interface PageState {
  entries: readonly AuditEntry[]
  nextCursor: string | null
  hasMore: boolean
}

const EMPTY_STATE: PageState = { entries: [], nextCursor: null, hasMore: false }

export default function AdminAuditLogPage() {
  const [state, setState] = useState<PageState>(EMPTY_STATE)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [agentIdFilter, setAgentIdFilter] = useState('')
  const [toolFilter, setToolFilter] = useState('')
  const [actionTypeFilter, setActionTypeFilter] = useState('')
  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>('')
  // Monotonic request id: typing into a filter field re-fires
  // ``fetchFirstPage`` faster than the network can answer, and a
  // slower in-flight response would otherwise clobber the freshest
  // filter's result. Capture the id at call time and bail if the ref
  // has advanced past it.
  const requestSeqRef = useRef(0)

  const filterParams = useMemo(
    () => ({
      agentId: agentIdFilter.trim() || null,
      toolName: toolFilter.trim() || null,
      actionType: actionTypeFilter.trim() || null,
      verdict: verdictFilter || null,
      limit: DEFAULT_PAGE_SIZE,
    }),
    [agentIdFilter, toolFilter, actionTypeFilter, verdictFilter],
  )

  const fetchFirstPage = useCallback(async () => {
    const seq = ++requestSeqRef.current
    setError(null)
    setLoading(true)
    try {
      const result = await listAuditEntries(filterParams)
      if (seq !== requestSeqRef.current) return
      setState({
        entries: result.data,
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      })
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      const message = getErrorMessage(err)
      log.error('listAuditEntries failed', { error: sanitizeForLog(message) })
      setError(message)
      setState(EMPTY_STATE)
    } finally {
      if (seq === requestSeqRef.current) setLoading(false)
    }
  }, [filterParams])

  const handleLoadMore = useCallback(async () => {
    if (!state.hasMore || !state.nextCursor || loadingMore) return
    // Bind the load-more to the current filter generation; a stale
    // page must not be appended after the user has changed filters.
    const seq = requestSeqRef.current
    setError(null)
    setLoadingMore(true)
    try {
      const result = await listAuditEntries({
        ...filterParams,
        cursor: state.nextCursor,
      })
      if (seq !== requestSeqRef.current) return
      setState((prev) => ({
        entries: [...prev.entries, ...result.data],
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      }))
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      const message = getErrorMessage(err)
      log.error('listAuditEntries load-more failed', { error: sanitizeForLog(message) })
      setError(message)
    } finally {
      if (seq === requestSeqRef.current) setLoadingMore(false)
    }
  }, [state.hasMore, state.nextCursor, loadingMore, filterParams])

  useEffect(() => {
    void fetchFirstPage()
  }, [fetchFirstPage])

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Security audit log"
        description="Tool-call evaluations recorded by the runtime security policy."
        count={state.entries.length}
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load audit log" description={error} />
      )}

      <SearchFilterSort
        search={
          <InputField
            label="Tool name"
            value={toolFilter}
            onValueChange={setToolFilter}
            placeholder="Filter by tool, e.g. file_system.write"
          />
        }
        filters={
          <div className="flex flex-wrap items-end gap-2">
            <InputField
              label="Agent ID"
              value={agentIdFilter}
              onValueChange={setAgentIdFilter}
              placeholder="agent_..."
            />
            <InputField
              label="Action type"
              value={actionTypeFilter}
              onValueChange={setActionTypeFilter}
              placeholder="invoke_tool / approve / ..."
            />
            <SelectField
              label="Verdict"
              value={verdictFilter}
              onChange={(value) => setVerdictFilter(value as VerdictFilter)}
              options={VERDICT_OPTIONS}
            />
          </div>
        }
      />

      {loading && state.entries.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : state.entries.length === 0 && error === null ? (
        <EmptyState
          icon={Shield}
          title="No audit entries match these filters"
          description="Loosen the filters above, or wait for the next tool evaluation to record."
        />
      ) : (
        <SectionCard title="Recent evaluations" icon={Shield}>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full table-fixed text-xs">
              <thead className="bg-surface text-left text-text-secondary">
                <tr>
                  <th className="w-44 px-3 py-2 font-medium">Time</th>
                  <th className="w-28 px-3 py-2 font-medium">Verdict</th>
                  <th className="w-28 px-3 py-2 font-medium">Risk</th>
                  <th className="w-44 px-3 py-2 font-medium">Agent</th>
                  <th className="w-48 px-3 py-2 font-medium">Action</th>
                  <th className="px-3 py-2 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {state.entries.map((entry) => (
                  <tr key={entry.id} className="align-top">
                    <td className="px-3 py-2 font-mono text-micro text-text-secondary">
                      {formatDateTime(entry.timestamp)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          entry.verdict === 'deny'
                            ? 'rounded bg-danger/10 px-1.5 py-0.5 font-medium uppercase text-danger'
                            : entry.verdict === 'escalate'
                              ? 'rounded bg-warning/10 px-1.5 py-0.5 font-medium uppercase text-warning'
                              : 'rounded bg-success/10 px-1.5 py-0.5 font-medium uppercase text-success'
                        }
                      >
                        {entry.verdict}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-medium uppercase text-text-secondary">
                      {entry.risk_level}
                    </td>
                    <td className="px-3 py-2 font-mono text-micro text-text-muted truncate" title={entry.agent_id ?? ''}>
                      {entry.agent_id ?? '-'}
                    </td>
                    <td className="px-3 py-2 font-mono text-micro text-text-muted truncate" title={entry.tool_name}>
                      {entry.tool_name}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">{entry.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {state.hasMore && (
            <div className="mt-3 flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </Button>
            </div>
          )}
        </SectionCard>
      )}
    </div>
  )
}
