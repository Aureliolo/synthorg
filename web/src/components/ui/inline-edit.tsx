import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { useFlash } from '@/hooks/useFlash'

type EditState = 'display' | 'editing' | 'saving'

export interface InlineEditProps {
  value: string
  onSave: (newValue: string) => Promise<void>
  /** Validation function -- return error string or null. */
  validate?: (value: string) => string | null
  placeholder?: string
  /** Custom render for the display value. */
  renderDisplay?: (value: string) => React.ReactNode
  /** Input type (default: "text"). */
  type?: 'text' | 'number'
  className?: string
  /** Whether editing is disabled. */
  disabled?: boolean
}

interface InlineEditMachine {
  state: EditState
  editValue: string
  error: string | null
  inputRef: React.RefObject<HTMLInputElement | null>
  flashClassName: string
  startEditing: () => void
  cancel: () => void
  save: () => Promise<void>
  setEditValue: (next: string) => void
  clearError: () => void
}

function useInlineEdit(
  value: string,
  onSave: (newValue: string) => Promise<void>,
  validate: ((value: string) => string | null) | undefined,
  disabled: boolean | undefined,
): InlineEditMachine {
  const [state, setState] = useState<EditState>('display')
  const [editValue, setEditValue] = useState(value)
  const [prevValue, setPrevValue] = useState(value)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const { flashClassName, triggerFlash } = useFlash()
  // Track whether save was triggered by Enter (to skip blur-triggered save).
  const saveInProgressRef = useRef(false)

  // Sync editValue when the prop changes externally while in display
  // mode. The "set state during render with a prevProp tracker" idiom
  // is React's documented pattern for prop->derived-state sync (see
  // "You Might Not Need an Effect" -> "Adjusting some state when a prop
  // changes"). Doing this via ``useEffect`` instead would trip
  // ``@eslint-react/set-state-in-effect`` and cause an extra commit
  // before the synced value is rendered.
  if (state === 'display' && value !== prevValue) {
    setPrevValue(value)
    setEditValue(value)
  }

  // Focus input when entering edit mode.
  useEffect(() => {
    if (state === 'editing') {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [state])

  const startEditing = useCallback(() => {
    if (disabled) return
    setEditValue(value)
    setError(null)
    setState('editing')
  }, [disabled, value])

  const cancel = useCallback(() => {
    setEditValue(value)
    setError(null)
    setState('display')
  }, [value])

  const save = useCallback(async () => {
    // Prevent double-save (Enter triggers save, then blur fires save again).
    if (saveInProgressRef.current || state !== 'editing') return
    if (editValue === value) {
      setState('display')
      setError(null)
      return
    }
    saveInProgressRef.current = true
    try {
      const validationError = validate ? validate(editValue) : null
      if (validationError) {
        setError(validationError)
        return
      }
      setState('saving')
      setError(null)
      await onSave(editValue)
      setState('display')
      triggerFlash()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
      setState('editing')
    } finally {
      saveInProgressRef.current = false
    }
  }, [editValue, onSave, validate, triggerFlash, state, value])

  return {
    state, editValue, error, inputRef, flashClassName,
    startEditing, cancel, save,
    setEditValue,
    clearError: () => setError(null),
  }
}

function InlineEditDisplay({
  value,
  placeholder,
  disabled,
  flashClassName,
  renderDisplay,
  onClick,
  className,
}: {
  value: string
  placeholder: string | undefined
  disabled: boolean | undefined
  flashClassName: string
  renderDisplay: ((value: string) => React.ReactNode) | undefined
  onClick: () => void
  className?: string
}) {
  return (
    <div className={cn('inline-block', className)}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        data-inline-display=""
        aria-label={`Edit: ${value || placeholder || 'empty'}`}
        className={cn(
          'cursor-pointer rounded-sm border-b border-dashed border-transparent text-left transition-colors',
          !disabled && 'hover:border-border-bright',
          !disabled && FOCUS_RING,
          disabled && 'cursor-default opacity-60',
          flashClassName,
        )}
      >
        {renderDisplay ? renderDisplay(value) : <span>{value || placeholder}</span>}
      </button>
    </div>
  )
}

function InlineEditField({
  machine,
  type,
  className,
}: {
  machine: InlineEditMachine
  type: 'text' | 'number'
  className?: string
}) {
  const errorId = useId()
  const { state, editValue, error, inputRef } = machine
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter') {
      e.preventDefault()
      void machine.save()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      machine.cancel()
    }
  }
  const handleBlur = () => {
    if (state !== 'editing') return
    void machine.save()
  }
  return (
    <div className={cn('inline-block', className)}>
      <div className="relative">
        <input
          ref={inputRef}
          type={type}
          value={editValue}
          onChange={(e) => {
            machine.setEditValue(e.target.value)
            machine.clearError()
          }}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={state === 'saving'}
          className={cn(
            'rounded-md border bg-surface px-2 py-1 text-sm text-foreground outline-none',
            FOCUS_RING,
            error ? 'border-danger' : 'border-border-bright',
            state === 'saving' && 'pointer-events-none opacity-60',
          )}
          aria-invalid={error ? true : undefined}
          aria-errormessage={error ? errorId : undefined}
        />
        {state === 'saving' && (
          <Loader2
            className="absolute top-1/2 right-2 size-3.5 -translate-y-1/2 animate-spin text-muted-foreground"
            aria-hidden="true"
          />
        )}
      </div>
      {error && (
        <p id={errorId} className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

export function InlineEdit({
  value,
  onSave,
  validate,
  placeholder,
  renderDisplay,
  type = 'text',
  className,
  disabled,
}: InlineEditProps) {
  const machine = useInlineEdit(value, onSave, validate, disabled)
  if (machine.state === 'display') {
    return (
      <InlineEditDisplay
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        flashClassName={machine.flashClassName}
        renderDisplay={renderDisplay}
        onClick={machine.startEditing}
        className={className}
      />
    )
  }
  return <InlineEditField machine={machine} type={type} className={className} />
}
