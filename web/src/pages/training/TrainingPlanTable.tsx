import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { StatusBadge } from '@/components/ui/status-badge'
import { ROUTES } from '@/router/routes'
import { formatDateTime, formatNumber } from '@/utils/format'
import { GraduationCap } from 'lucide-react'
import type {
  TrainingPlanResponse,
  TrainingResultResponse,
} from '@/api/endpoints/training'
import type { AgentRuntimeStatus } from '@/lib/utils'

export interface TrainingPlanRow {
  agentName: string
  plan: TrainingPlanResponse | null
  result: TrainingResultResponse | null
}

export interface TrainingPlanTableProps {
  rows: readonly TrainingPlanRow[]
  onExecute: (agentName: string) => void
}

function sumStored(result: TrainingResultResponse | null): number {
  if (!result) return 0
  return result.items_stored.reduce((sum, [, count]) => sum + count, 0)
}

function statusDot(plan: TrainingPlanResponse | null): AgentRuntimeStatus {
  if (!plan) return 'offline'
  switch (plan.status) {
    case 'executed':
      return 'active'
    case 'failed':
      return 'error'
    case 'pending':
      return 'idle'
    // Defensive fallback: a future `TrainingPlanStatus` variant should
    // map to a neutral dot rather than render `undefined`.
    default:
      return 'offline'
  }
}

interface TrainingPlanTableRowProps {
  row: TrainingPlanRow
  onExecute: (agentName: string) => void
}

function TrainingPlanTableRow({ row, onExecute }: TrainingPlanTableRowProps) {
  return (
    <tr>
      <td className="p-card">
        <Link
          to={`/agents/${encodeURIComponent(row.agentName)}`}
          className="font-medium text-foreground hover:text-accent"
        >
          {row.agentName}
        </Link>
      </td>
      <td className="p-card">
        <span className="inline-flex items-center gap-1.5">
          <StatusBadge status={statusDot(row.plan)} decorative />
          <span className="text-xs text-muted-foreground">
            {row.plan?.status ?? 'no plan'}
          </span>
        </span>
      </td>
      <td className="p-card text-right font-mono text-xs">
        {row.result ? formatNumber(row.result.source_agents_used.length) : '--'}
      </td>
      <td className="p-card text-right font-mono text-xs">
        {row.result ? formatNumber(sumStored(row.result)) : '--'}
      </td>
      <td className="p-card text-xs text-muted-foreground">
        {row.plan ? formatDateTime(row.plan.created_at) : '--'}
      </td>
      <td className="p-card text-right">
        {row.plan?.status === 'pending' ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onExecute(row.agentName)}
          >
            Execute
          </Button>
        ) : (
          <Button asChild size="sm" variant="ghost">
            <Link to={`/agents/${encodeURIComponent(row.agentName)}`}>
              Open
            </Link>
          </Button>
        )}
      </td>
    </tr>
  )
}

export function TrainingPlanTable({ rows, onExecute }: TrainingPlanTableProps) {
  const navigate = useNavigate()

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={GraduationCap}
        title="No training plans yet"
        description="Open an agent to customize and create a training plan."
        action={{
          label: 'Browse agents',
          onClick: () => { void navigate(ROUTES.AGENTS) },
        }}
      />
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            <th className="p-card text-left font-medium text-muted-foreground">Agent</th>
            <th className="p-card text-left font-medium text-muted-foreground">Status</th>
            <th className="p-card text-right font-medium text-muted-foreground">Sources</th>
            <th className="p-card text-right font-medium text-muted-foreground">Items stored</th>
            <th className="p-card text-left font-medium text-muted-foreground">Created</th>
            <th className="p-card text-right font-medium text-muted-foreground">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <TrainingPlanTableRow
              key={row.agentName}
              row={row}
              onExecute={onExecute}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
