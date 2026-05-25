import { createContext, use, useCallback, useId, useMemo, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
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
   * Unlike `leadingIcon`, pointer events pass through; consumers are
   * responsible for interactivity. Supplying a `trailingElement` on a
   * `type="password"` field replaces the built-in eye toggle.
   */
  trailingElement?: React.ReactNode
  /**
   * Suppress the built-in eye / eye-off visibility toggle on
   * `type="password"` fields. Default: false (toggle is shown). The
   * UX rule across the whole dashboard is that every password / secret
   * input MUST have a toggle; flip this only for the rare case where a
   * caller already supplies its own visibility affordance.
   */
  hidePasswordToggle?: boolean
}

interface TextareaProps extends BaseFieldProps, Omit<React.ComponentProps<'textarea'>, 'id'> {
  multiline: true
  ref?: React.Ref<HTMLTextAreaElement>
}

export type InputFieldProps = InputProps | TextareaProps

interface PasswordVisibilityContextValue {
  visible: boolean
  setVisible: (next: boolean) => void
}

const PasswordVisibilityContext = createContext<PasswordVisibilityContextValue | null>(null)

/**
 * Wraps a group of password / secret `<InputField>`s so a single eye
 * toggle reveals or hides every field in the group at once. Use it
 * for semantically-paired fields (e.g. Password + Confirm Password on
 * the Create Account screen) where independent toggles would be
 * surprising. Unrelated secrets (e.g. an OAuth client secret next to
 * a header value) should stay outside the provider so each toggles
 * on its own.
 */
export function PasswordVisibilityGroup({ children }: { children: React.ReactNode }) {
  const [visible, setVisible] = useState(false)
  const value = useMemo<PasswordVisibilityContextValue>(
    () => ({ visible, setVisible }),
    [visible],
  )
  return (
    <PasswordVisibilityContext value={value}>
      {children}
    </PasswordVisibilityContext>
  )
}

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

function PasswordToggleButton({
  visible,
  onToggle,
  disabled,
}: {
  visible: boolean
  onToggle: () => void
  disabled: boolean
}) {
  const Icon = visible ? EyeOff : Eye
  const label = visible ? 'Hide password' : 'Show password'
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      aria-pressed={visible}
      disabled={disabled}
      className={cn(
        'pointer-events-auto inline-flex items-center justify-center',
        'text-muted-foreground hover:text-foreground',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded',
        'disabled:cursor-not-allowed disabled:opacity-60',
      )}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}

function usePasswordVisibility(): { visible: boolean; toggle: () => void } {
  const groupContext = use(PasswordVisibilityContext)
  const [localVisible, setLocalVisible] = useState(false)
  const visible = groupContext !== null ? groupContext.visible : localVisible
  const toggle = useCallback(() => {
    if (groupContext !== null) {
      groupContext.setVisible(!groupContext.visible)
    } else {
      setLocalVisible((prev) => !prev)
    }
  }, [groupContext])
  return { visible, toggle }
}

interface TrailingArgs {
  trailingElement: InputProps['trailingElement']
  isPassword: boolean
  hidePasswordToggle: boolean | undefined
  visible: boolean
  toggleVisible: () => void
  disabled: boolean
}

function _renderTrailing(args: TrailingArgs): React.ReactNode {
  if (args.trailingElement != null && args.trailingElement !== false) return args.trailingElement
  if (!args.isPassword || args.hidePasswordToggle) return null
  return (
    <PasswordToggleButton
      visible={args.visible}
      onToggle={args.toggleVisible}
      disabled={args.disabled}
    />
  )
}

function InputBody({
  inputProps,
  ref,
  leadingIcon,
  renderedTrailing,
  inputClasses,
}: {
  inputProps: React.InputHTMLAttributes<HTMLInputElement>
  ref: React.Ref<HTMLInputElement> | undefined
  leadingIcon: React.ReactNode
  renderedTrailing: React.ReactNode
  inputClasses: string
}) {
  return (
    <div className="relative">
      {leadingIcon != null && leadingIcon !== false && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 flex -translate-y-1/2 items-center text-muted-foreground"
        >
          {leadingIcon}
        </span>
      )}
      <input ref={ref} {...inputProps} className={inputClasses} />
      {renderedTrailing !== null && (
        <span className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center">
          {renderedTrailing}
        </span>
      )}
    </div>
  )
}

interface InputIds {
  id: string
  errorId: string
  hintId: string
}

interface BuildInputPropsArgs {
  domProps: Omit<InputProps, 'label' | 'error' | 'hint' | 'className' | 'ref' | 'onValueChange' | 'onChange' | 'leadingIcon' | 'trailingElement' | 'hidePasswordToggle' | 'type' | 'multiline'>
  type: InputProps['type']
  isPassword: boolean
  visible: boolean
  hasError: boolean
  hint: string | undefined
  ids: InputIds
  handleChange: (event: React.ChangeEvent<HTMLInputElement>) => void
}

function _buildInputProps(args: BuildInputPropsArgs): React.InputHTMLAttributes<HTMLInputElement> {
  const { domProps, type, isPassword, visible, hasError, hint, ids, handleChange } = args
  return {
    ...domProps,
    id: ids.id,
    type: isPassword && visible ? 'text' : type,
    'aria-invalid': hasError ? true : (domProps['aria-invalid'] ?? false),
    'aria-errormessage': mergeAriaToken(
      domProps['aria-errormessage'],
      hasError ? ids.errorId : undefined,
    ),
    'aria-describedby': mergeAriaToken(
      domProps['aria-describedby'],
      hint && !hasError ? ids.hintId : undefined,
    ),
    onChange: handleChange,
  }
}

function InputVariant(props: InputProps) {
  const {
    label, error, hint, className, ref,
    onValueChange, onChange,
    leadingIcon, trailingElement, hidePasswordToggle, type,
    multiline: _multiline, ...domProps
  } = props
  void _multiline
  const id = useId()
  const ids: InputIds = { id, errorId: `${id}-error`, hintId: `${id}-hint` }
  const hasError = !!error
  const isPassword = type === 'password'
  const { visible, toggle } = usePasswordVisibility()
  const renderedTrailing = _renderTrailing({
    trailingElement,
    isPassword,
    hidePasswordToggle,
    visible,
    toggleVisible: toggle,
    disabled: Boolean(domProps.disabled),
  })
  const inputClasses = buildInputClasses({
    hasError,
    // Match the ``leadingIcon != null && leadingIcon !== false`` render
    // guards on the icon ``<span>`` below so the padding flag and the
    // actual JSX presence agree on every legal ReactNode value (incl.
    // ``0`` / ``''`` / ``false``).
    hasLeadingIcon: leadingIcon != null && leadingIcon !== false,
    hasTrailingElement: renderedTrailing !== null,
    className,
  })
  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onValueChange?.(event.target.value)
    onChange?.(event)
  }
  const inputProps = _buildInputProps({
    domProps, type, isPassword, visible, hasError, hint, ids, handleChange,
  })
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel htmlFor={ids.id} label={label} required={Boolean(domProps.required)} />
      <InputBody
        inputProps={inputProps}
        ref={ref}
        leadingIcon={leadingIcon}
        renderedTrailing={renderedTrailing}
        inputClasses={inputClasses}
      />
      <FieldHelp hintId={ids.hintId} errorId={ids.errorId} hint={hint} error={error} />
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
