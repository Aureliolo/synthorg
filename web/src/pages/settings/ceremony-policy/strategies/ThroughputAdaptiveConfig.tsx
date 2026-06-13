import { InputField } from '@/components/ui/input-field'

export interface ThroughputAdaptiveConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean
}

export function ThroughputAdaptiveConfig({ config, onChange, disabled }: ThroughputAdaptiveConfigProps) {
  const dropPct = typeof config['velocity_drop_threshold_pct'] === 'number' && Number.isFinite(config['velocity_drop_threshold_pct'])
    ? config['velocity_drop_threshold_pct']
    : 30
  const spikePct = typeof config['velocity_spike_threshold_pct'] === 'number' && Number.isFinite(config['velocity_spike_threshold_pct'])
    ? config['velocity_spike_threshold_pct']
    : 50
  const windowSize = typeof config['measurement_window_tasks'] === 'number' && Number.isFinite(config['measurement_window_tasks'])
    ? config['measurement_window_tasks']
    : 10

  return (
    <div className="space-y-3">
      <InputField
        label="Velocity Drop Threshold (%)"
        type="number"
        value={String(dropPct)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, velocity_drop_threshold_pct: Math.min(100, Math.max(1, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Trigger ceremony when velocity drops by this percentage (1-100)"
      />

      <InputField
        label="Velocity Spike Threshold (%)"
        type="number"
        value={String(spikePct)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, velocity_spike_threshold_pct: Math.min(100, Math.max(1, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Trigger ceremony when velocity spikes by this percentage (1-100)"
      />

      <InputField
        label="Measurement Window (tasks)"
        type="number"
        value={String(windowSize)}
        onChange={(e) => {
          const val = Number(e.target.value)
          if (!Number.isFinite(val)) return
          onChange({ ...config, measurement_window_tasks: Math.min(100, Math.max(2, Math.round(val))) })
        }}
        disabled={disabled}
        hint="Rolling window of task completions for rate calculation (2-100)"
      />
    </div>
  )
}
