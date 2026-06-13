/**
 * Read-only experiment registry explorer.
 *
 * The experiment registry is keyed by experiment name (there is no
 * list-all endpoint), so the operator enters a key to inspect its
 * registered variants and recorded assignment history. Variant
 * registration and assignment are backend-/agent-only writes and are not
 * surfaced here; see the experiments controller docstring.
 */
import { useCallback, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SkeletonText } from '@/components/ui/skeleton'
import { listAssignments, listVariants } from '@/api/endpoints/experiments'
import type { ExperimentAssignment, ExperimentVariant } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { formatDateTime, formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('ExperimentExplorer')

interface ExperimentData {
  variants: readonly ExperimentVariant[]
  assignments: readonly ExperimentAssignment[]
  loading: boolean
  error: string | null
  loaded: boolean
}

const EMPTY: ExperimentData = {
  variants: [],
  assignments: [],
  loading: false,
  error: null,
  loaded: false,
}

function VariantsTable({ variants }: { variants: readonly ExperimentVariant[] }) {
  return (
    <table className="w-full min-w-[28rem] text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
          <th className="py-2 pr-4 font-medium">Variant</th>
          <th className="py-2 pr-4 text-right font-medium">Weight</th>
          <th className="py-2 font-medium">Description</th>
        </tr>
      </thead>
      <tbody>
        {variants.map((v) => (
          <tr key={v.variant} className="border-t border-border">
            <td className="py-2 pr-4 font-medium text-foreground">{v.variant}</td>
            <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(v.weight)}</td>
            <td className="py-2 text-muted-foreground">{v.description || '--'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function AssignmentsTable({ assignments }: { assignments: readonly ExperimentAssignment[] }) {
  return (
    <table className="w-full min-w-[28rem] text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
          <th className="py-2 pr-4 font-medium">Subject</th>
          <th className="py-2 pr-4 font-medium">Variant</th>
          <th className="py-2 font-medium">Assigned</th>
        </tr>
      </thead>
      <tbody>
        {assignments.map((a) => (
          <tr key={`${a.subject_id}-${a.assigned_at}`} className="border-t border-border">
            <td className="py-2 pr-4 font-mono text-xs text-foreground">{a.subject_id}</td>
            <td className="py-2 pr-4 text-foreground">{a.variant}</td>
            <td className="py-2 text-xs text-muted-foreground">{formatDateTime(a.assigned_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ExperimentResults({ data }: { data: ExperimentData }) {
  if (data.loading) return <SkeletonText lines={4} />
  if (data.error) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load experiment"
        description={data.error}
      />
    )
  }
  if (!data.loaded) return null
  if (data.variants.length === 0 && data.assignments.length === 0) {
    return (
      <EmptyState
        icon={FlaskConical}
        title="No data for this experiment"
        description="No variants or assignments are registered under that key."
      />
    )
  }
  return (
    <div className="space-y-section-gap">
      {data.variants.length > 0 && (
        <div className="overflow-x-auto">
          <VariantsTable variants={data.variants} />
        </div>
      )}
      {data.assignments.length > 0 && (
        <div className="overflow-x-auto">
          <AssignmentsTable assignments={data.assignments} />
        </div>
      )}
    </div>
  )
}

export function ExperimentExplorer() {
  const [key, setKey] = useState('')
  const [data, setData] = useState<ExperimentData>(EMPTY)

  const load = useCallback((experiment: string) => {
    const trimmed = experiment.trim()
    if (trimmed === '') return
    setData({ ...EMPTY, loading: true })
    void Promise.allSettled([listVariants(trimmed), listAssignments(trimmed)]).then(
      ([variantsResult, assignmentsResult]) => {
        const failure = [variantsResult, assignmentsResult].find((r) => r.status === 'rejected')
        if (failure?.status === 'rejected') {
          const message = getErrorMessage(failure.reason)
          log.error('experiment load failed', { error: sanitizeForLog(message) })
          setData({ ...EMPTY, error: message, loaded: true })
          return
        }
        setData({
          variants: variantsResult.status === 'fulfilled' ? variantsResult.value : [],
          assignments: assignmentsResult.status === 'fulfilled' ? assignmentsResult.value : [],
          loading: false,
          error: null,
          loaded: true,
        })
      },
    )
  }, [])

  return (
    <form
      className="space-y-section-gap"
      onSubmit={(e) => {
        e.preventDefault()
        load(key)
      }}
    >
      <div className="flex items-end gap-grid-gap">
        <div className="flex-1">
          <InputField
            label="Experiment key"
            value={key}
            onChange={(e) => setKey(e.currentTarget.value)}
            placeholder="e.g. planner-temperature"
          />
        </div>
        <Button type="submit" disabled={key.trim() === '' || data.loading}>
          {data.loading ? 'Loading...' : 'View'}
        </Button>
      </div>
      <ExperimentResults data={data} />
    </form>
  )
}
