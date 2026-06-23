import { Sparkles } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import {
  GOAL_OPTIONS,
  OVERSIGHT_OPTIONS,
  type BuildGoal,
  type OversightPref,
} from './template-step-data'

function Chip({
  label,
  selected,
  onClick,
}: {
  label: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1.5 text-sm font-medium transition-colors',
        FOCUS_RING,
        selected
          ? 'border-accent bg-accent/10 text-accent'
          : 'border-border bg-card text-muted-foreground hover:bg-card-hover hover:text-foreground',
      )}
    >
      {label}
    </button>
  )
}

interface ChipRowProps<T extends string> {
  label: string
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}

function ChipRow<T extends string>({ label, options, value, onChange }: ChipRowProps<T>) {
  return (
    <div className="space-y-2">
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <Chip
            key={opt.value}
            label={opt.label}
            selected={opt.value === value}
            onClick={() => onChange(opt.value)}
          />
        ))}
      </div>
    </div>
  )
}

export interface IntentChipsProps {
  buildGoal: BuildGoal
  setBuildGoal: (value: BuildGoal) => void
  oversight: OversightPref
  setOversight: (value: OversightPref) => void
}

/**
 * "Recommend for me" intent capture: clickable chips for what the user is
 * building and how much oversight they want. Drives the hero recommendation
 * live -- distinct from the catalogue filters, which narrow the full list.
 */
export function IntentChips({
  buildGoal,
  setBuildGoal,
  oversight,
  setOversight,
}: IntentChipsProps) {
  // Clicking the active chip clears the selection back to 'any' (nothing
  // highlighted), so there is no permanently-selected "No preference" pill.
  const onGoal = (value: BuildGoal) => setBuildGoal(value === buildGoal ? 'any' : value)
  const onOversight = (value: OversightPref) =>
    setOversight(value === oversight ? 'any' : value)
  return (
    <div className="space-y-4 rounded-lg border border-border bg-card/40 p-card">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-accent" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-foreground">Recommend for me</h3>
        <span className="text-xs text-muted-foreground">
          Pick what fits and we&rsquo;ll surface the best match.
        </span>
      </div>
      <ChipRow label="I'm building" options={GOAL_OPTIONS} value={buildGoal} onChange={onGoal} />
      <div className="space-y-1.5">
        <ChipRow
          label="Oversight"
          options={OVERSIGHT_OPTIONS}
          value={oversight}
          onChange={onOversight}
        />
        <p className="text-xs text-muted-foreground">
          Sets a sensible starting posture; you can fine-tune autonomy per agent later in Settings.
        </p>
      </div>
    </div>
  )
}
