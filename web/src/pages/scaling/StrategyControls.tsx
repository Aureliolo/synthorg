import { ChevronDown, ChevronUp, Settings } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import { ToggleField } from '@/components/ui/toggle-field'
import { useScalingStore } from '@/stores/scaling'
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

/**
 * Move the strategy at ``index`` one slot earlier (``-1``) or later (``+1``)
 * and return the resulting name order, or ``null`` when the move is a no-op
 * (already at an edge). The conflict-resolution priority is the order of the
 * strategy names, highest first.
 */
function reorderedNames(
  strategies: readonly ScalingStrategyResponse[],
  index: number,
  delta: -1 | 1,
): string[] | null {
  const target = index + delta
  if (target < 0 || target >= strategies.length) return null
  const ordered = strategies.map((s) => s.name)
  const moved = ordered[index]!
  ordered.splice(index, 1)
  ordered.splice(target, 0, moved)
  return ordered
}

export function StrategyControls({ strategies }: StrategyControlsProps) {
  const setStrategyEnabled = useScalingStore((s) => s.setStrategyEnabled)
  const reorderPriority = useScalingStore((s) => s.reorderPriority)
  const mutating = useScalingStore((s) => s.mutating)

  // Render in priority order (lowest priority number first) so the reorder
  // arrows map intuitively to "more important" (up) / "less important" (down).
  const ordered = [...strategies].sort((a, b) => a.priority - b.priority)

  const move = (index: number, delta: -1 | 1) => {
    const next = reorderedNames(ordered, index, delta)
    if (next) void reorderPriority(next)
  }

  return (
    <SectionCard title="Strategies" icon={Settings}>
      <div className="flex flex-col gap-card-gap">
        {ordered.map((strategy, index) => (
          <div
            key={strategy.name}
            className="flex items-center justify-between gap-3 rounded-md border border-border p-card max-[479px]:flex-col max-[479px]:items-stretch"
          >
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">
                  {STRATEGY_LABELS[strategy.name] ?? strategy.name}
                </span>
                <StatusBadge status={statusFromEnabled(strategy.enabled)} />
              </div>
              <span className="text-sm text-muted-foreground">
                {STRATEGY_DESCRIPTIONS[strategy.name] ?? ''}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <div className="flex flex-col">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={mutating || index === 0}
                  onClick={() => move(index, -1)}
                  aria-label={`Increase priority of ${STRATEGY_LABELS[strategy.name] ?? strategy.name}`}
                >
                  <ChevronUp className="size-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  disabled={mutating || index === ordered.length - 1}
                  onClick={() => move(index, 1)}
                  aria-label={`Decrease priority of ${STRATEGY_LABELS[strategy.name] ?? strategy.name}`}
                >
                  <ChevronDown className="size-3.5" />
                </Button>
              </div>
              <ToggleField
                label={strategy.enabled ? 'Enabled' : 'Disabled'}
                checked={strategy.enabled}
                disabled={mutating}
                onChange={(checked) => void setStrategyEnabled(strategy.name, checked)}
              />
            </div>
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
