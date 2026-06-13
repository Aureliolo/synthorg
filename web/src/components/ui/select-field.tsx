import { useId } from 'react'
import { cn } from '@/lib/utils'

export interface SelectOption {
  readonly value: string
  readonly label: string
  readonly disabled?: boolean
}

export interface SelectFieldProps {
  label: string
  options: readonly SelectOption[]
  value: string
  onChange: (value: string) => void
  error?: string | null | undefined
  hint?: string | undefined
  disabled?: boolean | undefined
  required?: boolean | undefined
  className?: string | undefined
  placeholder?: string | undefined
}

export interface SelectFieldHelpProps {
  hintId: string
  errorId: string
  hint: string | undefined
  error: string | null | undefined
}

function SelectFieldHelp({
  hintId,
  errorId,
  hint,
  error,
}: SelectFieldHelpProps) {
  if (error) {
    return <p id={errorId} role="alert" className="text-xs text-danger">{error}</p>
  }
  if (hint) {
    return <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>
  }
  return null
}

export function SelectField({
  label,
  options,
  value,
  onChange,
  error,
  hint,
  disabled,
  required,
  className,
  placeholder,
}: SelectFieldProps) {
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const hasError = !!error
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        required={required}
        aria-required={required || undefined}
        aria-invalid={hasError}
        aria-errormessage={hasError ? errorId : undefined}
        aria-describedby={hint && !hasError ? hintId : undefined}
        className={cn(
          'w-full rounded-md border bg-surface px-3 py-2 text-sm text-foreground',
          'focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent',
          'disabled:opacity-60 disabled:cursor-not-allowed',
          hasError ? 'border-danger' : 'border-border',
          className,
        )}
      >
        {placeholder && (
          <option value="" disabled>{placeholder}</option>
        )}
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} disabled={opt.disabled}>
            {opt.label}
          </option>
        ))}
      </select>
      <SelectFieldHelp hintId={hintId} errorId={errorId} hint={hint} error={error} />
    </div>
  )
}
