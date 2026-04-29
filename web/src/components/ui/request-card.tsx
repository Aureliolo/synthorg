import type { ClientRequest } from '@/api/endpoints/clients'
import { Button } from '@/components/ui/button'

/**
 * Per-request row inside the Request Queue Kanban board.
 *
 * Renders the request title + identity line, then a state-conditional
 * action group ({@link onScope} / {@link onApprove} / {@link onReject})
 * for requests still in the early stages of the lifecycle. The
 * caller owns optimistic-action state via the {@link pending} map so
 * a per-request flight is reflected as a disabled action group
 * without re-rendering the whole queue.
 *
 * Extracted from {@link RequestQueuePage} per the
 * `web/CLAUDE.md` "no >8-line JSX inside .map()" rule (issue #1666).
 */
export interface RequestCardProps {
  request: ClientRequest
  /**
   * Per-request optimistic-flight flag. Disables every action button
   * for the matching request_id while a Scope / Approve / Reject
   * call is in flight to prevent double-submission.
   */
  pending: Record<string, boolean>
  onScope: (requestId: string) => void
  onApprove: (requestId: string) => void
  onReject: (requestId: string) => void
}

const _ACTIONABLE_STATUSES = new Set([
  'submitted',
  'triaging',
  'scoping',
])

const _SCOPE_STATUSES = new Set(['submitted', 'triaging'])

export function RequestCard({
  request,
  pending,
  onScope,
  onApprove,
  onReject,
}: RequestCardProps) {
  const isPending = !!pending[request.request_id]
  const showActions = _ACTIONABLE_STATUSES.has(request.status)
  const showScope = _SCOPE_STATUSES.has(request.status)
  return (
    <li className="space-y-2 rounded-md border border-border bg-card-hover p-card text-sm">
      <div className="font-medium text-foreground">
        {request.requirement.title}
      </div>
      <div className="text-xs text-text-secondary">
        {request.client_id} · {request.request_id.slice(0, 8)}
      </div>
      {showActions && (
        <div className="flex flex-wrap gap-2 pt-1">
          {showScope && (
            <Button
              size="sm"
              variant="outline"
              disabled={isPending}
              onClick={() => onScope(request.request_id)}
            >
              Scope
            </Button>
          )}
          <Button
            size="sm"
            disabled={isPending}
            onClick={() => onApprove(request.request_id)}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={isPending}
            onClick={() => onReject(request.request_id)}
          >
            Reject
          </Button>
        </div>
      )}
    </li>
  )
}
