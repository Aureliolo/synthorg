import { Route } from 'lucide-react'

import type { PlanItem } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { StatusPill } from '@/components/ui/status-pill'
import { computeWaves, planItemAnchorId } from '@/utils/plans'

/**
 * The plan as a timeline of execution waves: each wave is the work that unlocks
 * once the previous one lands, and items within a wave run in parallel. Makes
 * the plan's real structure legible instead of a flat list. Hidden when there
 * is only one wave (nothing to sequence).
 */
export function PlanTimeline({ items }: { items: readonly PlanItem[] }) {
  const waves = computeWaves(items)
  if (waves.length < 2) return null
  return (
    <SectionCard
      title="Execution timeline"
      icon={Route}
      action={<StatPill label="waves" value={waves.length} />}
    >
      <ol className="space-y-2">
        {waves.map((wave) => (
          <li key={wave.index} className="rounded-md border border-border p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">
                Wave {wave.index + 1}
              </span>
              {wave.items.length > 1 && (
                <StatusPill
                  tone="accent"
                  ariaLabel="These items have no dependency between them"
                >
                  {wave.items.length} in parallel
                </StatusPill>
              )}
            </div>
            <ul className="flex flex-col gap-1.5">
              {wave.items.map((item) => (
                <li key={item.id}>
                  <a
                    href={`#${planItemAnchorId(item.id)}`}
                    className="flex items-center gap-2 text-sm text-text-secondary transition-colors hover:text-foreground"
                  >
                    <span className="truncate">{item.title}</span>
                    {item.owner !== null && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        · {item.owner}
                      </span>
                    )}
                  </a>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </SectionCard>
  )
}
