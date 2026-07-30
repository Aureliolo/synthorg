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
  /**
   * Visually hide the label (kept for screen readers) when a column header or
   * adjacent text already names the control -- e.g. dense table rows.
   */
  hideLabel?: boolean | undefined
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

/** Placeholder text shown for an unset value when the caller supplies none. */
const UNSET_OPTION_LABEL = 'Select an option'

/**
 * Whether *value* corresponds to one of the rendered options.
 *
 * A native ``<select>`` given a value no option carries falls back to
 * displaying the first option, so the control shows a selection the caller
 * never made. Knowing this lets the control render an option for its own
 * value instead of silently adopting someone else's.
 */
function valueHasOption(
  value: string,
  options: readonly SelectOption[] | undefined,
  groups: readonly SelectOptionGroup[] | undefined,
): boolean {
  const rendered = groups ? groups.flatMap((group) => group.options) : (options ?? [])
  return rendered.some((opt) => opt.value === value)
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

/**
 * An option carrying the control's own value, when no real option does.
 *
 * Gives the browser something to select that matches ``value``, so the control
 * cannot fall back to displaying the first option instead. Disabled, because it
 * is the absence of a choice rather than one of the choices; an unmatched
 * non-empty value shows as itself so a stale selection is legible rather than
 * being reported as another option.
 */
function SelectFallbackOption({
  value,
  placeholder,
  options,
  groups,
}: {
  value: string
  placeholder: string | undefined
  options: readonly SelectOption[] | undefined
  groups: readonly SelectOptionGroup[] | undefined
}) {
  if (valueHasOption(value, options, groups)) return null
  const label = placeholder ?? (value === '' ? UNSET_OPTION_LABEL : value)
  return <option value={value} disabled>{label}</option>
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
      <SelectFallbackOption
        value={value}
        placeholder={props.placeholder}
        options={props.options}
        groups={props.groups}
      />
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
  hideLabel,
}: SelectFieldProps) {
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const hasError = !!error
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={hideLabel ? 'sr-only' : 'text-sm font-medium text-foreground'}
      >
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
