import { InputField } from '@/components/ui/input-field'

export interface BudgetDrivenConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean | undefined
}

export function BudgetDrivenConfig({ config, onChange, disabled }: BudgetDrivenConfigProps) {
  const thresholds = Array.isArray(config['budget_thresholds'])
    ? (config['budget_thresholds'] as unknown[]).map(Number).filter((n) => Number.isFinite(n))
    : [25, 50, 75, 100]
  const rawPct = typeof config['transition_threshold'] === 'number' && Number.isFinite(config['transition_threshold'])
    ? config['transition_threshold']
    : 100
  const transitionPct = Math.min(100, Math.max(1, rawPct))

  return (
    <div className="space-y-3">
      <InputField
        label="Budget Thresholds (%)"
        value={thresholds.join(', ')}
        onChange={(e) => {
          const parsed = e.target.value
            .split(',')
            .map((s) => Number(s.trim()))
            .filter((n) => Number.isFinite(n) && n > 0 && n <= 100)
          onChange({ ...config, budget_thresholds: parsed })
        }}
        disabled={disabled}
        hint="Comma-separated budget percentages that trigger ceremonies (e.g. 25, 50, 75)"
      />

      <InputField
        label="Transition Threshold (%)"
        type="number"
        value={String(transitionPct)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, transition_threshold: Math.min(100, Math.max(1, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Budget percentage that triggers sprint auto-transition (1-100)"
      />
    </div>
  )
}
