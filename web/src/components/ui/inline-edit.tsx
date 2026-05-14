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
  const [state, setState] = useState<EditState>('display')
  const [editValue, setEditValue] = useState(value)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const { flashClassName, triggerFlash } = useFlash()
  const errorId = useId()

  // Track whether save was triggered by Enter (to skip blur-triggered save)
  const saveInProgressRef = useRef(false)

  // Sync editValue when prop value changes externally while in display mode
  if (state === 'display' && value !== editValue) {
    setEditValue(value)
  }

  // Focus input when entering edit mode
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
    // Prevent double-save (Enter triggers save, then blur fires save again)
    if (saveInProgressRef.current) return
    if (state !== 'editing') return
    if (editValue === value) {
      setState('display')
      setError(null)
      return
    }
    saveInProgressRef.current = true

    try {
      // Validate
      if (validate) {
        const validationError = validate(editValue)
        if (validationError) {
          setError(validationError)
          return
        }
      }

      setState('saving')
      setError(null)

      await onSave(editValue)
      setState('display')
      triggerFlash()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Save failed'
      setError(message)
      setState('editing')
    } finally {
      saveInProgressRef.current = false
    }
  }, [editValue, onSave, validate, triggerFlash, state, value])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.nativeEvent.isComposing) return
      if (e.key === 'Enter') {
        e.preventDefault()
        void save()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        cancel()
      }
    },
    [save, cancel],
  )

  const handleBlur = useCallback(() => {
    // Skip if save is already in progress (Enter key) or not in editing state
    if (saveInProgressRef.current || state !== 'editing') return
    void save()
  }, [save, state])

  if (state === 'display') {
    return (
      <div className={cn('inline-block', className)}>
        <button
          type="button"
          onClick={startEditing}
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

  return (
    <div className={cn('inline-block', className)}>
      <div className="relative">
        <input
          ref={inputRef}
          type={type}
          value={editValue}
          onChange={(e) => {
            setEditValue(e.target.value)
            setError(null)
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
