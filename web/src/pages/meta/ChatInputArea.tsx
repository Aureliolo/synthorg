import { Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { cn } from '@/lib/utils'

export interface ChatInputAreaProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  disabled: boolean
  label: string
  placeholder: string
  className?: string
}

/** Shared single-line send box for the meta conversational surfaces. */
export function ChatInputArea({
  value,
  onChange,
  onSend,
  disabled,
  label,
  placeholder,
  className,
}: ChatInputAreaProps) {
  return (
    <div className={cn('flex gap-2', className)}>
      <div className="flex-1">
        <InputField
          label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
        />
      </div>
      <Button
        size="sm"
        onClick={onSend}
        disabled={!value.trim() || disabled}
        aria-label="Send message"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}
