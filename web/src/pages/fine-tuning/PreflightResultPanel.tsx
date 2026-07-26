import type { PreflightResult } from '@/api/types/fine-tuning'
import { StatusBadge } from '@/components/ui/status-badge'

const STATUS_MAP = {
  pass: 'active' as const,
  warn: 'idle' as const,
  fail: 'error' as const,
}

interface PreflightResultPanelProps {
  result: PreflightResult
}

export function PreflightResultPanel({ result }: PreflightResultPanelProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-card">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">Pre-flight Results</span>
        {result.can_proceed ? (
          <span className="inline-flex items-center gap-1.5">
            <StatusBadge status="active" />
            <span className="text-xs">Ready</span>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <StatusBadge status="error" />
            <span className="text-xs">Blocked</span>
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1">
        {result.checks.map((check) => (
          <div key={check.name} className="flex items-center gap-2 text-sm">
            <StatusBadge status={STATUS_MAP[check.status]} />
            <span className="text-foreground">{check.message}</span>
            {check.detail && (
              <span className="text-muted-foreground">({check.detail})</span>
            )}
          </div>
        ))}
      </div>
      {result.recommended_batch_size != null && (
        <p className="text-xs text-muted-foreground">
          Recommended batch size: {result.recommended_batch_size}
        </p>
      )}
    </div>
  )
}
