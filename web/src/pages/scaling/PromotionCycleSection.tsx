import { TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { SectionCard } from '@/components/ui/section-card'
import { useAuth } from '@/hooks/useAuth'
import { usePromotionStore } from '@/stores/promotion'
import { formatDateTime, formatRelativeTime } from '@/utils/format'
import type { PromotionRecordDTO } from '@/api/types'

const PROMOTION_ROLES = ['ceo', 'manager'] as const

export function PromotionCycleSection() {
  const { userRole } = useAuth()
  const canManage = userRole !== null && (PROMOTION_ROLES as readonly string[]).includes(userRole)
  const cycleResult = usePromotionStore((s) => s.cycleResult)
  const cycleRunning = usePromotionStore((s) => s.cycleRunning)
  const runCycle = usePromotionStore((s) => s.runCycle)

  return (
    <SectionCard
      title="Promotion cycle"
      icon={TrendingUp}
      action={
        canManage ? (
          <Button size="sm" onClick={() => void runCycle()} disabled={cycleRunning}>
            {cycleRunning ? 'Running...' : 'Run promotion cycle'}
          </Button>
        ) : undefined
      }
    >
      {!canManage ? (
        <p className="text-sm text-muted-foreground">
          Only CEO and Manager roles can run the promotion cycle.
        </p>
      ) : cycleResult === null ? (
        <p className="text-sm text-muted-foreground">
          Evaluate every active agent and apply qualifying seniority changes in one pass.
        </p>
      ) : cycleResult.length === 0 ? (
        <EmptyState
          title="No changes this cycle"
          description="No active agent met the criteria for a seniority change."
        />
      ) : (
        <ul className="space-y-2">
          {cycleResult.map((record) => (
            <CycleRecordRow key={record.id} record={record} />
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

function CycleRecordRow({ record }: { record: PromotionRecordDTO }) {
  const Icon = record.direction === 'promotion' ? TrendingUp : TrendingDown
  const tone = record.direction === 'promotion' ? 'text-success' : 'text-warning'
  return (
    <li className="flex items-center justify-between gap-3 text-sm">
      <span className="flex items-center gap-2">
        <Icon className={`size-3.5 ${tone}`} aria-hidden="true" />
        <span className="text-foreground">{record.agent_name}</span>
        <span className="text-text-secondary">
          {record.old_level} -&gt; {record.new_level}
        </span>
      </span>
      <time
        dateTime={record.effective_at}
        title={formatDateTime(record.effective_at)}
        className="text-micro text-muted-foreground"
      >
        {formatRelativeTime(record.effective_at)}
      </time>
    </li>
  )
}
