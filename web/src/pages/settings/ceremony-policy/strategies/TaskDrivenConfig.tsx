import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'

const TRIGGER_OPTIONS = [
  { value: '', label: 'Select trigger...' },
  { value: 'sprint_start', label: 'Sprint Start (one-shot)' },
  { value: 'sprint_end', label: 'Sprint End (one-shot)' },
  { value: 'sprint_midpoint', label: 'Sprint Midpoint (one-shot)' },
  { value: 'every_n_completions', label: 'Every N Completions' },
  { value: 'sprint_percentage', label: 'Sprint Percentage' },
] as const

export interface TaskDrivenConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean | undefined
}

export function TaskDrivenConfig({ config, onChange, disabled }: TaskDrivenConfigProps) {
  const trigger = typeof config['trigger'] === 'string' ? config['trigger'] : ''
  const everyN = typeof config['every_n_completions'] === 'number' && Number.isFinite(config['every_n_completions'])
    ? config['every_n_completions']
    : 5
  const pct = typeof config['sprint_percentage'] === 'number' && Number.isFinite(config['sprint_percentage'])
    ? config['sprint_percentage']
    : 50

  return (
    <div className="space-y-3">
      <SelectField
        label="Trigger"
        options={TRIGGER_OPTIONS}
        value={trigger}
        onChange={(v) => onChange({ ...config, trigger: v })}
        disabled={disabled}
        hint="When to fire this ceremony"
      />

      {trigger === 'every_n_completions' && (
        <InputField
          label="Every N Completions"
          type="number"
          value={String(everyN)}
          onChange={(e) => {
            const val = Number(e.target.value)
            if (!Number.isFinite(val)) return
            onChange({ ...config, every_n_completions: Math.max(1, Math.round(val)) })
          }}
          disabled={disabled}
          hint="Fire after every N task completions (min: 1)"
        />
      )}

      {trigger === 'sprint_percentage' && (
        <InputField
          label="Sprint Percentage"
          type="number"
          value={String(pct)}
          onChange={(e) => {
            const val = Number(e.target.value)
            if (!Number.isFinite(val)) return
            onChange({ ...config, sprint_percentage: Math.min(100, Math.max(1, Math.round(val))) })
          }}
          disabled={disabled}
          hint="Fire when this percentage of tasks complete (1-100)"
        />
      )}
    </div>
  )
}
