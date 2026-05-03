import { useId } from 'react'
import { cn } from '@/lib/utils'

interface BaseFieldProps {
  label: string
  error?: string | null
  hint?: string
  /** Convenience callback that receives the value string directly. */
  onValueChange?: (value: string) => void
}

interface InputProps extends BaseFieldProps, Omit<React.ComponentProps<'input'>, 'id'> {
  multiline?: false
  ref?: React.Ref<HTMLInputElement>
  /**
   * Decorative leading icon rendered inside the input. Positioned relative to
   * the input box (not the label), so it stays vertically centered on the
   * input. Receives `pointer-events-none` automatically.
   */
  leadingIcon?: React.ReactNode
  /**
   * Trailing element rendered inside the input (e.g. a clear button).
   * Unlike `leadingIcon`, pointer events pass through -- consumers are
   * responsible for interactivity.
   */
  trailingElement?: React.ReactNode
}

interface TextareaProps extends BaseFieldProps, Omit<React.ComponentProps<'textarea'>, 'id'> {
  multiline: true
  ref?: React.Ref<HTMLTextAreaElement>
}

export type InputFieldProps = InputProps | TextareaProps

/**
 * Merge a caller-supplied ARIA id token list with a component-managed
 * token, preserving both. Prevents caller overrides via ``...domProps``
 * from silently dropping the component's ``hintId``/``errorId`` so
 * screen readers continue to receive the validation text.
 */
function mergeAriaToken(
  incoming: string | undefined,
  managed: string | undefined,
): string | undefined {
  const tokens = new Set<string>()
  if (incoming) {
    for (const token of incoming.split(/\s+/)) {
      if (token) tokens.add(token)
    }
  }
  if (managed) tokens.add(managed)
  if (tokens.size === 0) return undefined
  return [...tokens].join(' ')
}

function buildInputClasses({
  hasError,
  hasLeadingIcon,
  hasTrailingElement,
  className,
}: {
  hasError: boolean
  hasLeadingIcon: boolean
  hasTrailingElement: boolean
  className: string | undefined
}): string {
  return cn(
    'w-full rounded-md border bg-surface px-3 py-2 text-sm text-foreground',
    'placeholder:text-muted-foreground',
    'focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent',
    'disabled:opacity-60 disabled:cursor-not-allowed',
    hasError ? 'border-danger' : 'border-border',
    hasLeadingIcon ? 'pl-8' : undefined,
    hasTrailingElement ? 'pr-8' : undefined,
    className,
  )
}

/**
 * The single ``InputField`` entry point dispatches to one of two pure
 * render variants based on the ``multiline`` discriminant. Splitting on
 * the discriminant inside the component body lets each branch see the
 * already-narrowed props, so we never need ``as`` casts to bend
 * ``HTMLInputElement`` <-> ``HTMLTextAreaElement`` ref / event types.
 */
export function InputField(props: InputFieldProps) {
  if (props.multiline) {
    return <TextareaVariant {...props} />
  }
  return <InputVariant {...props} />
}

function FieldLabel({
  htmlFor,
  label,
  required,
}: {
  htmlFor: string
  label: string
  required: boolean
}) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
      {label}
      {required && <span className="ml-0.5 text-danger">*</span>}
    </label>
  )
}

function FieldHelp({
  hintId,
  errorId,
  hint,
  error,
}: {
  hintId: string
  errorId: string
  hint: string | undefined
  error: string | null | undefined
}) {
  return (
    <>
      {hint && !error && (
        <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-danger">{error}</p>
      )}
    </>
  )
}

function InputVariant(props: InputProps) {
  const {
    label,
    error,
    hint,
    className,
    ref,
    onValueChange,
    onChange,
    leadingIcon,
    trailingElement,
    multiline: _multiline,
    ...domProps
  } = props
  void _multiline
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const hasError = !!error

  const inputClasses = buildInputClasses({
    hasError,
    hasLeadingIcon: !!leadingIcon,
    hasTrailingElement: !!trailingElement,
    className,
  })

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onValueChange?.(event.target.value)
    onChange?.(event)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel
        htmlFor={id}
        label={label}
        required={Boolean(domProps.required)}
      />
      <div className="relative">
        {leadingIcon != null && leadingIcon !== false && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 flex -translate-y-1/2 items-center text-muted-foreground"
          >
            {leadingIcon}
          </span>
        )}
        <input
          id={id}
          ref={ref}
          {...domProps}
          aria-invalid={hasError ? true : (domProps['aria-invalid'] ?? false)}
          aria-errormessage={mergeAriaToken(
            domProps['aria-errormessage'],
            hasError ? errorId : undefined,
          )}
          aria-describedby={mergeAriaToken(
            domProps['aria-describedby'],
            hint && !hasError ? hintId : undefined,
          )}
          className={inputClasses}
          onChange={handleChange}
        />
        {trailingElement != null && trailingElement !== false && (
          <span className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center">
            {trailingElement}
          </span>
        )}
      </div>
      <FieldHelp hintId={hintId} errorId={errorId} hint={hint} error={error} />
    </div>
  )
}

function TextareaVariant(props: TextareaProps) {
  const {
    label,
    error,
    hint,
    className,
    ref,
    onValueChange,
    onChange,
    multiline: _multiline,
    ...domProps
  } = props
  void _multiline
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const hasError = !!error

  const inputClasses = buildInputClasses({
    hasError,
    hasLeadingIcon: false,
    hasTrailingElement: false,
    className,
  })

  const handleChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    onValueChange?.(event.target.value)
    onChange?.(event)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel
        htmlFor={id}
        label={label}
        required={Boolean(domProps.required)}
      />
      <textarea
        id={id}
        ref={ref}
        {...domProps}
        aria-invalid={hasError ? true : (domProps['aria-invalid'] ?? false)}
        aria-errormessage={mergeAriaToken(
          domProps['aria-errormessage'],
          hasError ? errorId : undefined,
        )}
        aria-describedby={mergeAriaToken(
          domProps['aria-describedby'],
          hint && !hasError ? hintId : undefined,
        )}
        className={cn(inputClasses, 'resize-y')}
        onChange={handleChange}
      />
      <FieldHelp hintId={hintId} errorId={errorId} hint={hint} error={error} />
    </div>
  )
}
