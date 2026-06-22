import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'

export interface WebhookRetryBarProps {
  count: number
  retrying: boolean
  onClear: () => void
  onRetry: () => void
}

/** Bulk-action bar for retrying the selected webhook receipts. */
export function WebhookRetryBar({ count, retrying, onClear, onRetry }: WebhookRetryBarProps) {
  return (
    <BulkActionBar selectedCount={count} onClear={onClear} loading={retrying}>
      <Button size="sm" variant="default" onClick={onRetry} disabled={retrying} className="gap-1">
        <RefreshCw className={`size-3.5 ${retrying ? 'animate-spin' : ''}`} aria-hidden="true" />
        {retrying ? 'Retrying...' : 'Retry selected'}
      </Button>
    </BulkActionBar>
  )
}
