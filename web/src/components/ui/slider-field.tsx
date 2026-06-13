import { useId } from 'react'
import { cn } from '@/lib/utils'

export interface SliderFieldProps {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number | undefined
  /** Display format for the current value. Defaults to showing the raw number. */
  formatValue?: ((value: number) => string) | undefined
  disabled?: boolean | undefined
  className?: string | undefined
}

export function SliderField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  formatValue,
  disabled,
  className,
}: SliderFieldProps) {
  const id = useId()
  const displayValue = formatValue ? formatValue(value) : String(value)

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="flex items-center justify-between">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        <span className="font-mono text-sm font-semibold text-accent" aria-live="polite">
          {displayValue}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className={cn(
          'h-2 w-full cursor-pointer appearance-none rounded-full bg-border',
          'accent-accent',
          'disabled:opacity-60 disabled:cursor-not-allowed',
        )}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={displayValue}
      />
      <div className="flex justify-between text-compact text-muted-foreground">
        <span>{formatValue ? formatValue(min) : min}</span>
        <span>{formatValue ? formatValue(max) : max}</span>
      </div>
    </div>
  )
}
