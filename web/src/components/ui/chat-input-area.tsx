import { Send } from 'lucide-react'
import { useCallback, useRef, type KeyboardEvent } from 'react'

import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { useTextareaAutogrow } from '@/components/ui/use-textarea-autogrow'
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
  /**
   * Accessible name for the send button. Override when a page renders more
   * than one send box, so the two do not share one accessible name.
   */
  sendLabel?: string
  /** Visible rows for the multiline field. Default 2. */
  rows?: number
  className?: string
}

/**
 * Shared multiline send box for the meta conversational surfaces. Enter
 * sends; Shift+Enter inserts a newline so operators can compose paragraphs.
 * Enter while an IME composition is active (confirming a CJK candidate, an
 * AZERTY dead key, etc.) never sends: it only commits the candidate.
 */
export function ChatInputArea({
  value,
  onChange,
  onSend,
  disabled,
  inputDisabled = false,
  label,
  placeholder,
  sendLabel = 'Send message',
  rows = 2,
  className,
}: ChatInputAreaProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useTextareaAutogrow(textareaRef, value)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // ``isComposing`` guards IME candidate confirmation: a bare Enter that
      // commits a composition must edit the field, never send the message.
      // The Send button's own guards (non-empty, not disabled) apply to Enter
      // too, so the two paths cannot diverge into a whitespace/ineligible send.
      if (
        e.key === 'Enter' &&
        !e.shiftKey &&
        !e.nativeEvent.isComposing &&
        !disabled &&
        !inputDisabled &&
        value.trim()
      ) {
        e.preventDefault()
        onSend()
      }
    },
    [disabled, inputDisabled, onSend, value],
  )
  return (
    <div className={cn('flex items-end gap-2', className)}>
      <div className="flex-1">
        <InputField
          ref={textareaRef}
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
        aria-label={sendLabel}
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}
