import { useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'

export interface SegmentedControlOption<T extends string = string> {
  readonly value: T
  readonly label: string
  readonly disabled?: boolean
}

export interface SegmentedControlProps<T extends string = string> {
  /** Accessible label for the control group. */
  label: string
  /** Available options. */
  options: readonly SegmentedControlOption<T>[]
  /** Currently selected value. */
  value: T
  /** Called when the user selects a different option. */
  onChange: (value: T) => void
  /** Disable all options. */
  disabled?: boolean
  /** Size variant. */
  size?: 'sm' | 'md'
  className?: string
}

function SegmentOption<T extends string>({
  option,
  selected,
  groupDisabled,
  size,
  onChange,
}: {
  option: SegmentedControlOption<T>
  selected: boolean
  groupDisabled: boolean
  size: 'sm' | 'md'
  onChange: (value: T) => void
}) {
  const optionDisabled = groupDisabled || option.disabled
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-value={option.value}
      disabled={optionDisabled}
      tabIndex={selected ? 0 : -1}
      onClick={() => {
        if (!optionDisabled) onChange(option.value)
      }}
      className={cn(
        'rounded-md font-medium transition-colors whitespace-nowrap',
        size === 'sm' ? 'text-xs px-2 py-1' : 'text-sm px-3 py-1.5',
        selected
          ? 'bg-accent/10 text-accent'
          : 'text-text-muted hover:text-foreground',
        optionDisabled && 'opacity-40 cursor-not-allowed',
      )}
    >
      {option.label}
    </button>
  )
}

function _arrowKeyDirection(key: string): -1 | 0 | 1 {
  if (key === 'ArrowRight' || key === 'ArrowDown') return 1
  if (key === 'ArrowLeft' || key === 'ArrowUp') return -1
  return 0
}

export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
  size = 'sm',
  className,
}: SegmentedControlProps<T>) {
  const groupRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (disabled) return
      const enabledOptions = options.filter((o) => !o.disabled)
      const currentIndex = enabledOptions.findIndex((o) => o.value === value)
      if (currentIndex === -1) return
      const direction = _arrowKeyDirection(e.key)
      if (direction === 0) return
      e.preventDefault()
      const nextIndex = (currentIndex + direction + enabledOptions.length) % enabledOptions.length
      const next = enabledOptions[nextIndex]
      if (!next) return
      onChange(next.value)
      const buttons = groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')
      const targetButton = Array.from(buttons ?? []).find(
        (btn) => btn.dataset['value'] === next.value,
      )
      targetButton?.focus()
    },
    [disabled, options, value, onChange],
  )

  return (
    <fieldset className={cn('border-none p-0 m-0', className)} disabled={disabled}>
      <legend className="sr-only">{label}</legend>
      <div
        ref={groupRef}
        role="radiogroup"
        aria-label={label}
        onKeyDown={handleKeyDown}
        className={cn(
          'inline-flex rounded-lg border border-border bg-background p-0.5 gap-0.5',
          disabled && 'opacity-60 pointer-events-none',
        )}
      >
        {options.map((option) => (
          <SegmentOption
            key={option.value}
            option={option}
            selected={option.value === value}
            groupDisabled={disabled}
            size={size}
            onChange={onChange}
          />
        ))}
      </div>
    </fieldset>
  )
}
