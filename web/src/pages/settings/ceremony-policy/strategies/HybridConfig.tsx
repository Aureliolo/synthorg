import { TaskDrivenConfig } from './TaskDrivenConfig'
import { CalendarConfig } from './CalendarConfig'

export interface HybridConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean | undefined
}

export function HybridConfig({ config, onChange, disabled }: HybridConfigProps) {
  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-muted">
          Task-Driven (ceiling)
        </p>
        <TaskDrivenConfig config={config} onChange={onChange} disabled={disabled} />
      </div>
      <div className="border-t border-border pt-4">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-muted">
          Calendar (floor)
        </p>
        <CalendarConfig config={config} onChange={onChange} disabled={disabled} />
      </div>
    </div>
  )
}
