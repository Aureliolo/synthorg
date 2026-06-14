/** Ceremony-policy + velocity display constants. */

import type { CeremonyStrategyType, VelocityCalcType } from '@/api/types/ceremony-policy'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

export const CEREMONY_STRATEGY_LABELS: Readonly<Record<CeremonyStrategyType, string>> = {
  task_driven: 'Task Driven',
  calendar: 'Calendar',
  hybrid: 'Hybrid',
  event_driven: 'Event Driven',
  budget_driven: 'Budget Driven',
  throughput_adaptive: 'Throughput Adaptive',
  external_trigger: 'External Trigger',
  milestone_driven: 'Milestone Driven',
}

export const CEREMONY_STRATEGY_DESCRIPTIONS: Readonly<Record<CeremonyStrategyType, string>> = {
  task_driven: 'Ceremonies fire at task-count milestones. Natural fit for agent speed.',
  calendar: 'Traditional time-based scheduling using wall-clock cadence.',
  hybrid: 'Calendar + task-driven, whichever fires first wins.',
  event_driven: 'Ceremonies subscribe to engine events with configurable debounce.',
  budget_driven: 'Ceremonies fire at cost-consumption thresholds.',
  throughput_adaptive: 'Ceremonies fire when throughput rate changes significantly.',
  external_trigger: 'Ceremonies fire on external signals (webhooks, git events, MCP).',
  milestone_driven: 'Ceremonies fire at semantic project milestones.',
}

export const VELOCITY_CALC_LABELS: Readonly<Record<VelocityCalcType, string>> = {
  task_driven: 'Per Task (pts/task)',
  calendar: 'Per Day (pts/day)',
  multi_dimensional: 'Multi-Dimensional (pts/sprint)',
  budget: `Per Currency Unit (pts/${DEFAULT_CURRENCY})`,
  points_per_sprint: 'Points per Sprint',
}

export const VELOCITY_UNIT_LABELS: Readonly<Record<VelocityCalcType, string>> = {
  task_driven: 'pts/task',
  calendar: 'pts/day',
  multi_dimensional: 'pts/sprint',
  budget: `pts/${DEFAULT_CURRENCY}`,
  points_per_sprint: 'pts/sprint',
}

export const STRATEGY_DEFAULT_VELOCITY_CALC: Readonly<Record<CeremonyStrategyType, VelocityCalcType>> = {
  task_driven: 'task_driven',
  calendar: 'calendar',
  hybrid: 'multi_dimensional',
  event_driven: 'points_per_sprint',
  budget_driven: 'budget',
  throughput_adaptive: 'task_driven',
  external_trigger: 'points_per_sprint',
  milestone_driven: 'points_per_sprint',
}

export const CEREMONY_STRATEGY_TYPES: readonly CeremonyStrategyType[] = [
  'task_driven',
  'calendar',
  'hybrid',
  'event_driven',
  'budget_driven',
  'throughput_adaptive',
  'external_trigger',
  'milestone_driven',
] as const

export const VELOCITY_CALC_TYPES: readonly VelocityCalcType[] = [
  'task_driven',
  'calendar',
  'multi_dimensional',
  'budget',
  'points_per_sprint',
] as const
