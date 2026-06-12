import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'

const FREQUENCY_OPTIONS = [
  { value: '', label: 'Select frequency...' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'bi_weekly', label: 'Bi-Weekly' },
  { value: 'per_sprint_day', label: 'Per Sprint Day' },
  { value: 'monthly', label: 'Monthly' },
] as const

export interface CalendarConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean
}

export function CalendarConfig({ config, onChange, disabled }: CalendarConfigProps) {
  const frequency = typeof config['frequency'] === 'string' ? config['frequency'] : ''
  const durationDays = typeof config['duration_days'] === 'number' && Number.isFinite(config['duration_days'])
    ? config['duration_days']
    : 14

  return (
    <div className="space-y-3">
      <SelectField
        label="Frequency"
        options={FREQUENCY_OPTIONS}
        value={frequency}
        onChange={(v) => onChange({ ...config, frequency: v })}
        disabled={disabled}
        hint="How often ceremonies fire"
      />

      <InputField
        label="Duration (days)"
        type="number"
        value={String(durationDays)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, duration_days: Math.min(90, Math.max(1, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Sprint duration in calendar days (1-90)"
      />
    </div>
  )
}
