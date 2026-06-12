import { InputField } from '@/components/ui/input-field'

export interface EventDrivenConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean
}

export function EventDrivenConfig({ config, onChange, disabled }: EventDrivenConfigProps) {
  const debounceDefault = typeof config['debounce_default'] === 'number' && Number.isFinite(config['debounce_default'])
    ? config['debounce_default']
    : 5
  const transitionEvent = typeof config['transition_event'] === 'string' ? config['transition_event'] : ''

  return (
    <div className="space-y-3">
      <InputField
        label="Default Debounce"
        type="number"
        value={String(debounceDefault)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, debounce_default: Math.min(10000, Math.max(1, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Events required before firing (1-10000)"
      />

      <InputField
        label="Transition Event"
        value={transitionEvent}
        onChange={(e) => onChange({ ...config, transition_event: e.target.value })}
        disabled={disabled}
        hint="Event name that triggers sprint auto-transition (e.g. sprint_backlog_empty)"
      />
    </div>
  )
}
