import { useCallback, useRef, useState, type KeyboardEvent, type ClipboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface TagInputProps {
  value: string[]
  onChange: (value: string[]) => void
  disabled?: boolean
  placeholder?: string
  className?: string
}

interface TagInputHandlers {
  draft: string
  setDraft: (value: string) => void
  handleKeyDown: (e: KeyboardEvent<HTMLInputElement>) => void
  handlePaste: (e: ClipboardEvent<HTMLInputElement>) => void
  removeAt: (index: number) => void
}

function useTagInputHandlers(
  value: string[],
  onChange: (value: string[]) => void,
): TagInputHandlers {
  const [draft, setDraft] = useState('')

  const addItems = useCallback(
    (items: string[]) => {
      const seen = new Set(value)
      const unique: string[] = []
      for (const raw of items) {
        const item = raw.trim()
        if (item && !seen.has(item)) {
          seen.add(item)
          unique.push(item)
        }
      }
      if (unique.length > 0) {
        onChange([...value, ...unique])
      }
    },
    [value, onChange],
  )

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        if (draft.trim()) {
          addItems([draft])
          setDraft('')
        }
      } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
        onChange(value.slice(0, -1))
      }
    },
    [draft, value, onChange, addItems],
  )

  const handlePaste = useCallback(
    (e: ClipboardEvent<HTMLInputElement>) => {
      const text = e.clipboardData.getData('text/plain')
      if (text.includes(',') || text.includes('\n')) {
        e.preventDefault()
        addItems(text.split(/[,\n]/))
        setDraft('')
      }
    },
    [addItems],
  )

  const removeAt = useCallback(
    (index: number) => {
      onChange(value.filter((_, i) => i !== index))
    },
    [value, onChange],
  )

  return { draft, setDraft, handleKeyDown, handlePaste, removeAt }
}

export function TagInput({ value, onChange, disabled, placeholder, className }: TagInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const { draft, setDraft, handleKeyDown, handlePaste, removeAt } = useTagInputHandlers(value, onChange)
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1.5',
        'focus-within:ring-2 focus-within:ring-accent/40',
        disabled && 'opacity-50',
        className,
      )}
      onClick={() => inputRef.current?.focus()}
      role="group"
      aria-label={placeholder ?? 'Tags'}
    >
      {value.map((item, i) => (
        <TagChip
          key={_stableTagKey(value, item, i)}
          item={item}
          disabled={disabled}
          onRemove={() => removeAt(i)}
        />
      ))}
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        disabled={disabled}
        placeholder={value.length === 0 ? placeholder : undefined}
        aria-label={placeholder ?? 'Tags input'}
        className="min-w-20 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-text-muted"
      />
    </div>
  )
}

function _stableTagKey(value: readonly string[], item: string, index: number): string {
  // Stable key: value + occurrence index for duplicates.
  const occurrence = value.slice(0, index).filter((v) => v === item).length
  return `${item}::${occurrence}`
}

function TagChip({
  item,
  disabled,
  onRemove,
}: {
  item: string
  disabled: boolean | undefined
  onRemove: () => void
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-accent/10 px-1.5 py-0.5 text-xs font-medium text-accent">
      {item}
      {!disabled && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onRemove()
          }}
          className="rounded-sm hover:bg-accent/20"
          aria-label={`Remove ${item}`}
        >
          <X className="size-3" aria-hidden="true" />
        </button>
      )}
    </span>
  )
}
