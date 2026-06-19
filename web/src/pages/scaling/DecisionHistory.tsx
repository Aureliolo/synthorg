import { Clock } from 'lucide-react'

import { EmptyState } from '@/components/ui/empty-state'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import type { ScalingDecisionResponse } from '@/api/endpoints/scaling'
import type { AgentRuntimeStatus } from '@/utils/agent-status'

interface DecisionHistoryProps {
  decisions: readonly ScalingDecisionResponse[]
}

const ACTION_STATUS_MAP: Record<string, AgentRuntimeStatus> = {
  hire: 'active',
  prune: 'error',
  hold: 'idle',
  no_op: 'offline',
}

function clampPercent(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n * 100)))
}

interface DecisionRowProps {
  decision: ScalingDecisionResponse
}

function DecisionRow({ decision }: DecisionRowProps) {
  return (
    <tr>
      <td className="py-2 pr-2">
        <div className="flex items-center gap-2">
          <StatusBadge
            status={ACTION_STATUS_MAP[decision.action_type] ?? 'idle'}
          />
          <span className="font-medium text-foreground">
            {decision.action_type.toUpperCase()}
          </span>
        </div>
      </td>
      <td className="py-2 pr-2 text-muted-foreground">{decision.rationale}</td>
      <td className="py-2 pr-2 text-muted-foreground">
        {decision.source_strategy}
      </td>
      <td className="py-2 text-right text-muted-foreground">
        {clampPercent(decision.confidence)}%
      </td>
    </tr>
  )
}

export function DecisionHistory({ decisions }: DecisionHistoryProps) {
  return (
    <SectionCard title="Recent Decisions" icon={Clock}>
      {decisions.length === 0 ? (
        <EmptyState
          title="No recent decisions"
          description="Trigger an evaluation to generate scaling decisions."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground uppercase">
                <th scope="col" className="py-2 pr-2">
                  Action
                </th>
                <th scope="col" className="py-2 pr-2">
                  Rationale
                </th>
                <th scope="col" className="py-2 pr-2">
                  Strategy
                </th>
                <th scope="col" className="py-2 text-right">
                  Confidence
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {decisions.map((d) => (
                <DecisionRow key={d.id} decision={d} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  )
}
