import { CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatLatency } from '@/utils/providers'
import type { TestConnectionResponse } from '@/api/types/providers'

interface TestConnectionResultProps {
  result: TestConnectionResponse
  className?: string
}

export function TestConnectionResult({ result, className }: TestConnectionResultProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={cn(
        'flex items-center gap-2 rounded-md p-card text-sm',
        result.success
          ? 'bg-success/10 text-success'
          : 'bg-danger/10 text-danger',
        className,
      )}
    >
      {result.success ? (
        <>
          <CheckCircle2 className="size-4 shrink-0" />
          <span>
            Connected{result.model_tested ? ` (${result.model_tested})` : ''}
            {result.latency_ms != null ? ` - ${formatLatency(result.latency_ms)}` : ''}
          </span>
        </>
      ) : (
        <>
          <XCircle className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 break-words">{result.error ?? 'Connection failed'}</span>
        </>
      )}
    </div>
  )
}
