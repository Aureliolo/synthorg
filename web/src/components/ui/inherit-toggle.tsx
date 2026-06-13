import { useId } from 'react'
import { cn, FOCUS_RING } from '@/lib/utils'

export interface InheritToggleProps {
  /** Whether inheritance is active (true = inherit, false = override). */
  inherit: boolean
  /** Called when the toggle changes. */
  onChange: (inherit: boolean) => void
  /** Label for the inherit source (e.g. "project"). */
  inheritFrom?: string | undefined
  disabled?: boolean | undefined
  className?: string | undefined
}

export function InheritToggle({
  inherit,
  onChange,
  inheritFrom = 'project',
  disabled,
  className,
}: InheritToggleProps) {
  const id = useId()

  return (
    <div className={cn('flex items-center gap-3', className)}>
      <button
        id={id}
        role="switch"
        type="button"
        aria-label={inherit ? `Inherit from ${inheritFrom}` : 'Override'}
        aria-checked={!inherit}
        onClick={() => onChange(!inherit)}
        disabled={disabled}
        className={cn(
          'relative h-5 w-9 shrink-0 rounded-full transition-colors',
          FOCUS_RING,
          'disabled:opacity-60 disabled:cursor-not-allowed',
          !inherit ? 'bg-accent' : 'bg-border',
        )}
      >
        <span
          className={cn(
            'block h-4 w-4 rounded-full bg-foreground transition-transform',
            !inherit ? 'translate-x-4' : 'translate-x-0.5',
          )}
        />
      </button>
      <label
        htmlFor={id}
        className={cn(
          'text-sm font-medium',
          disabled ? 'cursor-default text-muted-foreground' : 'cursor-pointer text-foreground',
        )}
      >
        {inherit ? `Inherit from ${inheritFrom}` : 'Override'}
      </label>
    </div>
  )
}
