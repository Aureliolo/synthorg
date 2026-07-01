import { Sparkles } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface ExamplePromptsProps {
  /** Prompt texts rendered as clickable suggestion chips. */
  prompts: readonly string[]
  /** Called with the chosen prompt text (typically fills the input). */
  onSelect: (prompt: string) => void
  disabled?: boolean
  className?: string
}

/**
 * Clickable example-prompt chips for an empty conversational surface.
 *
 * Gives first-time users concrete starting points instead of a blank
 * input; selecting a chip hands the text to the caller, which decides
 * whether to prefill the input or send immediately.
 */
export function ExamplePrompts({
  prompts,
  onSelect,
  disabled = false,
  className,
}: ExamplePromptsProps) {
  if (prompts.length === 0) return null
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Sparkles className="size-3.5" aria-hidden />
        Try one of these
      </span>
      <div className="flex flex-wrap gap-2" role="list" aria-label="Example prompts">
        {prompts.map((prompt) => (
          <Button
            key={prompt}
            type="button"
            role="listitem"
            size="sm"
            variant="outline"
            disabled={disabled}
            className="h-auto whitespace-normal py-1.5 text-left font-normal text-text-secondary"
            onClick={() => onSelect(prompt)}
          >
            {prompt}
          </Button>
        ))}
      </div>
    </div>
  )
}
