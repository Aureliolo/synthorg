import { useId } from 'react'
import { cn, FOCUS_RING, mergeAriaToken } from '@/lib/utils'

export interface ToggleFieldProps {
  label: string
  description?: string | undefined
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean | undefined
  className?: string | undefined
  /**
   * Ids of external text describing the control, merged with the field's
   * own description id rather than replacing it.
   */
  describedBy?: string | undefined
}

export function ToggleField({
  label,
  description,
  checked,
  onChange,
  disabled,
  className,
  describedBy,
}: ToggleFieldProps) {
  const id = useId()
  const descriptionId = `${id}-description`

  return (
    <div className={cn('flex items-start gap-3', className)}>
      <button
        id={id}
        role="switch"
        type="button"
        aria-checked={checked}
        aria-describedby={mergeAriaToken(describedBy, description ? descriptionId : undefined)}
        onClick={() => onChange(!checked)}
        disabled={disabled}
        className={cn(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors',
          FOCUS_RING,
          'disabled:opacity-60 disabled:cursor-not-allowed',
          checked ? 'bg-accent' : 'bg-border',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            // Tailwind v4 `translate-x-*` emits the CSS `translate:` property,
            // not `transform:`. Using `transition-transform` here leaves the
            // knob snapping instantly -- target `translate` explicitly so the
            // slide actually animates.
            'block h-4 w-4 rounded-full bg-foreground transition-[translate]',
            checked ? 'translate-x-4' : 'translate-x-0.5',
          )}
        />
      </button>
      <div className="flex flex-col gap-0.5">
        <label htmlFor={id} className={cn('text-sm font-medium', disabled ? 'cursor-default text-muted-foreground' : 'cursor-pointer text-foreground')}>
          {label}
        </label>
        {description && (
          <p id={descriptionId} className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
    </div>
  )
}
