import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ChatErrorNoticeProps {
  message: string
  onRetry: () => void
}

/**
 * Shared error state for the conversational panels. Renders distinctly from an
 * assistant reply (danger-toned notice + icon) so a failed turn is never
 * mistaken for a model answer, and offers a Try-again that re-sends the last
 * user message.
 */
export function ChatErrorNotice({ message, onRetry }: ChatErrorNoticeProps) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="flex flex-1 flex-col gap-2">
        <p>{message}</p>
        <Button variant="outline" size="sm" className="self-start" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  )
}
