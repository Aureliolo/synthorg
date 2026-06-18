import { useId } from 'react'
import { cn } from '@/lib/utils'

export interface SelectOption {
  readonly value: string
  readonly label: string
  readonly disabled?: boolean
}

export interface SelectOptionGroup {
  readonly label: string
  readonly options: readonly SelectOption[]
}

export interface SelectFieldProps {
  label: string
  options?: readonly SelectOption[]
  /**
   * Grouped options rendered as native ``<optgroup>`` elements. When
   * provided, takes precedence over ``options`` (the two are mutually
   * exclusive). Additive: existing ``options`` callers are unaffected.
   */
  groups?: readonly SelectOptionGroup[] | undefined
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

function SelectOptionItem({ opt }: { opt: SelectOption }) {
  return (
    <option value={opt.value} disabled={opt.disabled}>
      {opt.label}
    </option>
  )
}

function SelectFieldOptions({
  options,
  groups,
}: {
  options: readonly SelectOption[]
  groups: readonly SelectOptionGroup[] | undefined
}) {
  if (groups) {
    return (
      <>
        {groups.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.options.map((opt) => (
              <SelectOptionItem key={opt.value} opt={opt} />
            ))}
          </optgroup>
        ))}
      </>
    )
  }
  return (
    <>
      {options.map((opt) => (
        <SelectOptionItem key={opt.value} opt={opt} />
      ))}
    </>
  )
}

interface SelectControlProps {
  id: string
  errorId: string
  hintId: string
  hasError: boolean
  value: string
  onChange: (value: string) => void
  disabled: boolean | undefined
  required: boolean | undefined
  hint: string | undefined
  className: string | undefined
  placeholder: string | undefined
  options: readonly SelectOption[] | undefined
  groups: readonly SelectOptionGroup[] | undefined
}

function SelectControl(props: SelectControlProps) {
  const { id, errorId, hintId, hasError, value, onChange } = props
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={props.disabled}
      required={props.required}
      aria-required={props.required || undefined}
      aria-invalid={hasError}
      aria-errormessage={hasError ? errorId : undefined}
      aria-describedby={props.hint && !hasError ? hintId : undefined}
      className={cn(
        'w-full rounded-md border bg-surface px-3 py-2 text-sm text-foreground',
        'focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent',
        'disabled:opacity-60 disabled:cursor-not-allowed',
        hasError ? 'border-danger' : 'border-border',
        props.className,
      )}
    >
      {props.placeholder && (
        <option value="" disabled>{props.placeholder}</option>
      )}
      <SelectFieldOptions options={props.options ?? []} groups={props.groups} />
    </select>
  )
}

export function SelectField({
  label,
  options,
  groups,
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
      <SelectControl
        id={id}
        errorId={errorId}
        hintId={hintId}
        hasError={hasError}
        value={value}
        onChange={onChange}
        disabled={disabled}
        required={required}
        hint={hint}
        className={className}
        placeholder={placeholder}
        options={options}
        groups={groups}
      />
      <SelectFieldHelp hintId={hintId} errorId={errorId} hint={hint} error={error} />
    </div>
  )
}
