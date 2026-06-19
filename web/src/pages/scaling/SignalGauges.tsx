import { memo } from 'react'
import { Activity } from 'lucide-react'

import { ProgressGauge } from '@/components/ui/progress-gauge'
import { SectionCard } from '@/components/ui/section-card'
import type { ScalingSignalResponse } from '@/api/endpoints/scaling'

interface SignalGaugesProps {
  signals: readonly ScalingSignalResponse[]
}

function findSignal(
  signals: readonly ScalingSignalResponse[],
  name: string,
): number {
  return signals.find((s) => s.name === name)?.value ?? 0
}

function clampGauge(value: number, max: number): number {
  return Math.max(0, Math.min(max, Math.round(value)))
}

export const SignalGauges = memo(function SignalGauges({
  signals,
}: SignalGaugesProps) {
  const utilization = findSignal(signals, 'avg_utilization') * 100
  const burnRate = findSignal(signals, 'burn_rate_percent')
  const decliningCount = findSignal(signals, 'declining_agent_count')

  return (
    <SectionCard title="Signal Dashboard" icon={Activity}>
      <div className="grid grid-cols-3 gap-card-gap max-[639px]:grid-cols-1">
        <div className="flex flex-col items-center gap-2">
          <ProgressGauge value={clampGauge(utilization, 100)} max={100} />
          <span className="text-sm font-medium text-foreground">
            Utilization
          </span>
        </div>
        <div className="flex flex-col items-center gap-2">
          <ProgressGauge value={clampGauge(burnRate, 100)} max={100} />
          <span className="text-sm font-medium text-foreground">
            Budget Burn
          </span>
        </div>
        <div className="flex flex-col items-center gap-2">
          <ProgressGauge
            value={clampGauge(decliningCount, 10)}
            max={10}
          />
          <span className="text-sm font-medium text-foreground">
            Declining Agents
          </span>
        </div>
      </div>
    </SectionCard>
  )
})
