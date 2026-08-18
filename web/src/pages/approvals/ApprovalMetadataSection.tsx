import type { ApprovalResponse } from '@/api/types/approvals'
import { visibleMetadataEntries } from '@/utils/approvals'

function metadataValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  return ''
}

/**
 * Whatever the producing feature stamped onto the approval, minus the database
 * keys. The map is open-ended, so the section renders keys nobody chose to put
 * in front of an operator; the ones that are references stay out of the page
 * and keep driving the deep links instead.
 */
export function ApprovalMetadataSection({ approval }: { approval: ApprovalResponse }) {
  const entries = visibleMetadataEntries(approval.metadata)
  if (entries.length === 0) return null
  return (
    <div>
      <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        Metadata
      </span>
      <dl className="mt-1 space-y-1">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-center gap-2 text-xs">
            <dt className="font-mono text-muted-foreground">{key}:</dt>
            <dd className="text-text-secondary">{metadataValue(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
