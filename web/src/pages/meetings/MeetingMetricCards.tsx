import { useMemo } from 'react'
import { MetricCard } from '@/components/ui/metric-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { cn } from '@/lib/utils'
import { formatTokenCount } from '@/utils/format'
import { countByStatus, totalTokensUsed } from '@/utils/meetings'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingMetricCardsProps {
  meetings: readonly MeetingResponse[]
  className?: string
}

export function MeetingMetricCards({ meetings, className }: MeetingMetricCardsProps) {
  const total = meetings.length
  // Three O(n) passes over the meetings list: memoise so they only re-run
  // when the list itself changes, not on every parent re-render.
  const { inProgress, completed, tokens } = useMemo(
    () => ({
      inProgress: countByStatus(meetings, 'in_progress'),
      completed: countByStatus(meetings, 'completed'),
      tokens: totalTokensUsed(meetings),
    }),
    [meetings],
  )

  return (
    <StaggerGroup className={cn('grid grid-cols-2 gap-grid-gap md:grid-cols-3 lg:grid-cols-4', className)}>
      <StaggerItem>
        <MetricCard label="TOTAL MEETINGS" value={total} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="IN PROGRESS" value={inProgress} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="COMPLETED" value={completed} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="TOTAL TOKENS" value={formatTokenCount(tokens)} />
      </StaggerItem>
    </StaggerGroup>
  )
}
