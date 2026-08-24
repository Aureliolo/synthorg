/**
 * How each agent's own calls actually went, side by side.
 *
 * The comparison is only valid because an agent is a fixed (role, model)
 * unit: while a turn could be re-dispatched onto different horsepower under
 * the same name, these rows would be averages over runs that never belonged
 * together.
 *
 * Rows are grouped by role and model so "two agents on the same model" and
 * "the same role on two models" both read off the page, and a cell below the
 * operator's sample floor says so rather than showing a rate the sample
 * cannot support.
 */
import { Users } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { StatusPill } from '@/components/ui/status-pill'
import type { DispatchProfile } from '@/api/types/agents'
import { formatNumber } from '@/utils/format'
import { formatRatePercent, formatLatency } from '@/utils/providers'
import {
  useDispatchProfiles,
  type DispatchProfilesController,
} from './useDispatchProfiles'

function groupKey(row: DispatchProfile): string {
  return `${row.role} on ${row.provider_name} / ${row.model}`
}

function ProfileRow({ row }: { row: DispatchProfile }) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <div className="text-sm font-medium text-foreground">{row.agent_name}</div>
        <div className="text-xs text-muted-foreground">{row.department}</div>
      </th>
      <td className="py-2 pr-4 align-top tabular-nums">
        {formatNumber(row.call_count)}
      </td>
      <td className="py-2 pr-4 align-top">
        {row.has_enough_calls ? (
          <span className="tabular-nums">
            {formatRatePercent(row.success_rate_percent)}
          </span>
        ) : (
          // A rate over a handful of calls is not a measurement; showing it
          // beside one over hundreds invites a decision it cannot support.
          <StatusPill tone="text-secondary">Not enough calls</StatusPill>
        )}
      </td>
      <td className="py-2 pr-4 align-top tabular-nums">
        {row.has_enough_calls ? formatLatency(row.latency?.p50_ms ?? null) : '-'}
      </td>
      <td className="py-2 align-top tabular-nums">
        {row.has_enough_calls ? formatLatency(row.latency?.p99_ms ?? null) : '-'}
      </td>
    </tr>
  )
}

function ComparisonGroup({
  label,
  rows,
}: {
  label: string
  rows: readonly DispatchProfile[]
}) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-xs font-medium text-muted-foreground">{label}</h4>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-border text-xs font-medium text-muted-foreground">
              <th scope="col" className="py-2 pr-4 font-medium">Agent</th>
              <th scope="col" className="py-2 pr-4 font-medium">Calls</th>
              <th scope="col" className="py-2 pr-4 font-medium">Success rate</th>
              <th scope="col" className="py-2 pr-4 font-medium">p50</th>
              <th scope="col" className="py-2 font-medium">p99</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <ProfileRow key={row.agent_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function groupRows(
  rows: readonly DispatchProfile[],
): readonly (readonly [string, readonly DispatchProfile[]])[] {
  const groups = new Map<string, DispatchProfile[]>()
  for (const row of rows) {
    const key = groupKey(row)
    const bucket = groups.get(key)
    if (bucket === undefined) groups.set(key, [row])
    else bucket.push(row)
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}

function ComparisonBody({ ctrl }: { ctrl: DispatchProfilesController }) {
  const { state } = ctrl
  if (state.loading) return <SkeletonText lines={4} />
  if (state.error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load the comparison"
        description={state.error}
        onRetry={ctrl.load}
      />
    )
  }
  if (state.rows.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No active agents"
        description="A comparison needs a roster; hire an agent and its own calls start accumulating from the first one."
      />
    )
  }
  return (
    <div className="flex flex-col gap-section-gap">
      {groupRows(state.rows).map(([label, rows]) => (
        <ComparisonGroup key={label} label={label} rows={rows} />
      ))}
    </div>
  )
}

export function DispatchComparisonSection() {
  const ctrl = useDispatchProfiles()
  return (
    <SectionCard title="Dispatch comparison" icon={Users}>
      <ComparisonBody ctrl={ctrl} />
    </SectionCard>
  )
}
