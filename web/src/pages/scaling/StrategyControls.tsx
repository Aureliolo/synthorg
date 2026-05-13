import { Settings } from 'lucide-react'

import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import type { ScalingStrategyResponse } from '@/api/endpoints/scaling'
import type { AgentRuntimeStatus } from '@/lib/utils'

function statusFromEnabled(enabled: boolean): AgentRuntimeStatus {
  return enabled ? 'active' : 'offline'
}

interface StrategyControlsProps {
  strategies: readonly ScalingStrategyResponse[]
}

const STRATEGY_LABELS: Record<string, string> = {
  workload: 'Workload Auto-Scale',
  budget_cap: 'Budget Cap',
  skill_gap: 'Skill Gap',
  performance_pruning: 'Performance Pruning',
}

const STRATEGY_DESCRIPTIONS: Record<string, string> = {
  workload: 'Hire when utilization exceeds threshold, prune when below floor',
  budget_cap: 'Hard ceiling on spend: blocks hires and triggers prunes',
  skill_gap: 'Identify missing skills from task requirements',
  performance_pruning: 'Prune agents with sustained performance regression',
}

export function StrategyControls({ strategies }: StrategyControlsProps) {
  return (
    <SectionCard title="Strategies" icon={Settings}>
      <div className="flex flex-col gap-card-gap">
        {strategies.map((strategy) => (
          <div
            key={strategy.name}
            className="flex items-center justify-between rounded-md border border-border p-card"
          >
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">
                  {STRATEGY_LABELS[strategy.name] ?? strategy.name}
                </span>
                <StatusBadge
                  status={statusFromEnabled(strategy.enabled)}
                />
              </div>
              <span className="text-sm text-muted-foreground">
                {STRATEGY_DESCRIPTIONS[strategy.name] ?? ''}
              </span>
            </div>
            <span className="text-sm text-muted-foreground">
              Priority: {strategy.priority}
            </span>
          </div>
        ))}
        {strategies.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No strategies configured
          </p>
        )}
      </div>
    </SectionCard>
  )
}
