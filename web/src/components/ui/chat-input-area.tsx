import { Send } from 'lucide-react'
import { useCallback, type KeyboardEvent } from 'react'

import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { cn } from '@/lib/utils'

export interface ChatInputAreaProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  /** Disables the Send button (send blocked: in-flight, or a precondition
   *  such as no agent selected). The text field stays editable so composed
   *  text is never discarded. */
  disabled: boolean
  /** Freezes the text field itself, for terminal states where further input
   *  is meaningless (e.g. a closed conversation). Implies {@link disabled}. */
  inputDisabled?: boolean
  label: string
  placeholder: string
  /** Visible rows for the multiline field. Default 2. */
  rows?: number
  className?: string
}

/**
 * Shared multiline send box for the meta conversational surfaces. Enter
 * sends; Shift+Enter inserts a newline so operators can compose paragraphs.
 */
export function ChatInputArea({
  value,
  onChange,
  onSend,
  disabled,
  inputDisabled = false,
  label,
  placeholder,
  rows = 2,
  className,
}: ChatInputAreaProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        onSend()
      }
    },
    [onSend],
  )
  return (
    <div className={cn('flex items-end gap-2', className)}>
      <div className="flex-1">
        <InputField
          label={label}
          multiline
          rows={rows}
          value={value}
          onValueChange={onChange}
          placeholder={placeholder}
          onKeyDown={handleKeyDown}
          disabled={inputDisabled}
        />
      </div>
      <Button
        size="sm"
        onClick={onSend}
        disabled={!value.trim() || disabled || inputDisabled}
        aria-label="Send message"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}
