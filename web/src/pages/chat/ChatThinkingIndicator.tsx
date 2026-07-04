import { useState } from 'react'

import { ProgressIndicator } from '@/components/ui/progress-indicator'

export interface ChatThinkingIndicatorProps {
  /** Verb shown beside the elapsed timer (e.g. "Thinking", "Working"). */
  label?: string
}

/**
 * In-flight indicator for a chat turn: an indeterminate bar plus the
 * seconds elapsed since the turn started, replacing the old static
 * "Thinking..." pulse so a long turn reads as progressing rather than
 * stalled.
 *
 * The component renders only while a turn is in flight, so its mount time
 * is the turn's start; capturing it in a ref keeps the timer stable across
 * re-renders without threading a timestamp through the store.
 */
export function ChatThinkingIndicator({
  label = 'Thinking',
}: ChatThinkingIndicatorProps) {
  // A lazy state initialiser (not ``new Date()`` in the render body) keeps
  // the render pure while capturing the turn's start exactly once.
  const [startedAt] = useState<Date>(() => new Date())
  return (
    <div className="mr-8 rounded-md bg-card p-card">
      <ProgressIndicator
        variant="indeterminate"
        label={label}
        startedAt={startedAt}
      />
    </div>
  )
}
