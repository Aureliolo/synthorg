import type { ComponentType } from 'react'
import type { CeremonyStrategyType } from '@/api/types/ceremony-policy'
import { TaskDrivenConfig } from './strategies/TaskDrivenConfig'
import { CalendarConfig } from './strategies/CalendarConfig'
import { HybridConfig } from './strategies/HybridConfig'
import { EventDrivenConfig } from './strategies/EventDrivenConfig'
import { BudgetDrivenConfig } from './strategies/BudgetDrivenConfig'
import { ThroughputAdaptiveConfig } from './strategies/ThroughputAdaptiveConfig'
import { ExternalTriggerConfig } from './strategies/ExternalTriggerConfig'
import { MilestoneDrivenConfig } from './strategies/MilestoneDrivenConfig'

export interface StrategyConfigPanelProps {
  strategy: CeremonyStrategyType
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean | undefined
}

type StrategyConfigBody = ComponentType<{
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean | undefined
}>

// Table-driven dispatch: the exhaustive Record makes TypeScript enforce a
// component for every strategy (replacing the old switch + assertNever).
const STRATEGY_COMPONENTS: Record<CeremonyStrategyType, StrategyConfigBody> = {
  task_driven: TaskDrivenConfig,
  calendar: CalendarConfig,
  hybrid: HybridConfig,
  event_driven: EventDrivenConfig,
  budget_driven: BudgetDrivenConfig,
  throughput_adaptive: ThroughputAdaptiveConfig,
  external_trigger: ExternalTriggerConfig,
  milestone_driven: MilestoneDrivenConfig,
}

export function StrategyConfigPanel({ strategy, config, onChange, disabled }: StrategyConfigPanelProps) {
  const Body = STRATEGY_COMPONENTS[strategy]
  return <Body config={config} onChange={onChange} disabled={disabled} />
}
