import { Gavel } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { CompletionOracleVerdictBadge } from '@/components/ui/completion-oracle-verdict-badge'
import { EmptyState } from '@/components/ui/empty-state'
import { RedTeamVerdictBadge } from '@/components/ui/red-team-verdict-badge'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { formatRelativeTime } from '@/utils/format'

import {
  useGateVerdicts,
  type GateKind,
  type GateVerdictRow,
  type GateVerdictsController,
} from './useGateVerdicts'

const GATE_COPY = {
  completion_oracle: {
    title: 'Peer-review verdicts',
    emptyTitle: 'No reviews yet',
    emptyDescription:
      'This agent holds the Completion Reviewer role but has not been selected for a review yet.',
  },
  red_team: {
    title: 'Adversarial verdicts',
    emptyTitle: 'No attacks yet',
    emptyDescription:
      'This agent holds the Red Team role but has not been selected for an evaluation yet.',
  },
} as const satisfies Record<GateKind, { title: string; emptyTitle: string; emptyDescription: string }>

const VERDICT_LABELS: Record<string, string> = {
  approve: 'Approved',
  approve_with_notes: 'With notes',
  reject: 'Rejected',
  escalate: 'Escalated',
  pass: 'Passed',
  pass_with_findings: 'With findings',
  block: 'Blocked',
}

export interface GateVerdictsPanelProps {
  agentId: string
  gate: GateKind
  className?: string
}

/**
 * One gate-role agent's verdict record.
 *
 * Rendered only for an agent holding a gate role: judging is what the role
 * is for, so an agent that does not hold one has no verdicts to compare.
 */
export function GateVerdictsPanel({ agentId, gate, className }: GateVerdictsPanelProps) {
  const ctrl = useGateVerdicts(agentId, gate)
  if (ctrl.loading) return null

  return (
    <SectionCard title={GATE_COPY[gate].title} icon={Gavel} className={className}>
      <GateVerdictsBody ctrl={ctrl} />
    </SectionCard>
  )
}

function GateVerdictsBody({ ctrl }: { ctrl: GateVerdictsController }) {
  if (ctrl.loadError) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-danger">
          Failed to load this agent&apos;s verdicts. The record is unknown rather
          than empty: retry before drawing a conclusion from it.
        </p>
        <Button variant="outline" size="sm" onClick={() => void ctrl.refetch()}>
          Retry
        </Button>
      </div>
    )
  }
  if (ctrl.summary === null || ctrl.summary.total === 0) {
    return (
      <EmptyState
        title={GATE_COPY[ctrl.gate].emptyTitle}
        description={GATE_COPY[ctrl.gate].emptyDescription}
      />
    )
  }
  return (
    <div className="space-y-4">
      <VerdictTally summary={ctrl.summary.by_verdict} total={ctrl.summary.total} />
      <RecentVerdicts rows={ctrl.recent} />
    </div>
  )
}

function VerdictTally({
  summary,
  total,
}: {
  summary: Record<string, number>
  total: number
}) {
  return (
    <div className="flex flex-wrap gap-grid-gap">
      <StatPill label="Total" value={total} />
      {Object.entries(summary)
        .filter(([, count]) => count > 0)
        .map(([verdict, count]) => (
          <StatPill
            key={verdict}
            label={VERDICT_LABELS[verdict] ?? verdict}
            value={count}
          />
        ))}
    </div>
  )
}

function RecentVerdicts({ rows }: { rows: readonly GateVerdictRow[] }) {
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <VerdictListItem key={row.key} row={row} />
      ))}
    </ul>
  )
}

function VerdictListItem({ row }: { row: GateVerdictRow }) {
  return (
    <li className="flex flex-wrap items-center gap-2 border-b border-border pb-2 last:border-b-0 last:pb-0">
      <VerdictBadge row={row} />
      <span className="font-mono text-compact text-muted-foreground">{row.taskId}</span>
      <span className="text-compact text-muted-foreground">
        {formatRelativeTime(row.recordedAt)}
      </span>
      {row.modelId !== null && (
        <StatPill
          label={row.provider ?? 'model'}
          value={
            row.capability === null ? row.modelId : `${row.modelId} (${row.capability})`
          }
        />
      )}
      <p className="basis-full text-xs text-muted-foreground">{row.summary}</p>
    </li>
  )
}

function VerdictBadge({ row }: { row: GateVerdictRow }) {
  if (row.gate === 'completion_oracle') {
    return <CompletionOracleVerdictBadge verdict={row.verdict} />
  }
  return <RedTeamVerdictBadge verdict={row.verdict} />
}
