import { TriangleAlert, UserRound, UsersRound } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import { UNKNOWN_AGENT_NAME } from '@/utils/agents'
import { derivePlanStaffing, type StaffingEntry } from '@/utils/plans'

function StaffingRow({ entry }: { entry: StaffingEntry }) {
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
      <UserRound className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="text-sm font-medium text-foreground">
        {entry.ownerName ?? UNKNOWN_AGENT_NAME}
      </span>
      <span className="text-xs text-text-secondary">
        {entry.itemCount} item{entry.itemCount === 1 ? '' : 's'}
        {entry.highStakesCount > 0 && ` · ${entry.highStakesCount} high-stakes`}
      </span>
      {entry.overloaded && (
        <StatusPill tone="warning" icon={TriangleAlert} className="ml-auto">
          Bottleneck
        </StatusPill>
      )}
    </li>
  )
}

/**
 * Who the plan staffs: each owning role's item load, how much of it is
 * high-stakes, and any owner carrying a bottleneck share, plus a banner when
 * items are left unassigned. Hidden when no item has an owner and none are
 * unassigned (an empty plan has nothing to staff).
 */
export function PlanStaffingPanel({ plan }: { plan: Plan }) {
  const staffing = derivePlanStaffing(plan.items)
  if (staffing.roles.length === 0 && staffing.unassigned === 0) return null
  return (
    <SectionCard
      title="Staffing"
      icon={UsersRound}
      action={
        <StatusPill tone="text-secondary">
          {staffing.totalOwners} owner{staffing.totalOwners === 1 ? '' : 's'}
        </StatusPill>
      }
    >
      <div className="space-y-3">
        {staffing.unassigned > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-warning">
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
            {staffing.unassigned} item{staffing.unassigned === 1 ? '' : 's'} left
            unassigned: no role owns {staffing.unassigned === 1 ? 'it' : 'them'} yet.
          </p>
        )}
        {staffing.roles.length > 0 && (
          <ul className="flex flex-col gap-2">
            {staffing.roles.map((entry) => (
              <StaffingRow key={entry.owner} entry={entry} />
            ))}
          </ul>
        )}
      </div>
    </SectionCard>
  )
}
