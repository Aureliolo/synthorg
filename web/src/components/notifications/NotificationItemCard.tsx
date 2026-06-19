import { memo } from 'react'
import { AlertTriangle, Check, Info, X, XCircle } from 'lucide-react'
import { Link } from 'react-router'

import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'
import type { NotificationItem, NotificationSeverity } from '@/types/notifications'

const SEVERITY_ICONS: Record<NotificationSeverity, React.ElementType> = {
  info: Info,
  warning: AlertTriangle,
  error: XCircle,
  critical: XCircle,
}

const SEVERITY_COLORS: Record<NotificationSeverity, string> = {
  info: 'text-accent',
  warning: 'text-warning',
  error: 'text-danger',
  critical: 'text-danger',
}

const BORDER_COLORS: Record<NotificationSeverity, string> = {
  info: 'border-l-accent',
  warning: 'border-l-warning',
  error: 'border-l-danger',
  critical: 'border-l-danger',
}

interface NotificationItemCardProps {
  readonly item: NotificationItem
  readonly onMarkRead: (id: string) => void
  readonly onDismiss: (id: string) => void
}

function _safeInternalHref(href: string | undefined): string | null {
  // Only accept internal absolute paths (a single leading slash, not
  // the protocol-relative ``//host`` form). External / malformed hrefs
  // collapse to ``null`` so we never hand them to react-router's <Link>.
  if (!href || !href.startsWith('/') || href.startsWith('//')) return null
  return href
}

function NotificationContent({ item }: { item: NotificationItem }) {
  const Icon = SEVERITY_ICONS[item.severity]
  return (
    <>
      <Icon
        className={cn('mt-0.5 size-4 shrink-0', SEVERITY_COLORS[item.severity])}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1">
        {/* Severity surfaced as an sr-only prefix so the accessible
            name still mentions it without overriding the rich text
            content the way a hardcoded ``aria-label`` would. The
            click target's accessible name is otherwise derived from
            the visible title + description + timestamp. */}
        <span className="sr-only">{`${item.severity} notification: `}</span>
        <span className="block truncate text-sm font-medium text-foreground">
          {item.title}
        </span>
        {item.description && (
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {item.description}
          </span>
        )}
        <span className="mt-1 block text-xs text-muted-foreground/70">
          {formatRelativeTime(item.timestamp)}
        </span>
      </span>
    </>
  )
}

function NotificationMainTarget({
  item,
  safeHref,
  innerClassName,
  onMarkRead,
}: {
  item: NotificationItem
  safeHref: string | null
  innerClassName: string
  onMarkRead: (id: string) => void
}) {
  const content = <NotificationContent item={item} />
  // Three render modes for the main click target:
  //   1. ``<Link>`` when the notification carries a safe internal
  //      href: native anchor semantics (right-click open in new tab,
  //      Cmd-click) and react-router takes care of the transition.
  //   2. ``<button>`` when the only side-effect is marking as read
  //      (no navigation): native keyboard support without a shim.
  //   3. Plain ``<div>`` when the row is inert (already read AND no
  //      valid href): no role, not focusable, no hover affordance.
  const markOnInteract = (): void => {
    if (!item.read) onMarkRead(item.id)
  }
  if (safeHref) {
    return (
      <Link to={safeHref} onClick={markOnInteract} className={innerClassName}>
        {content}
      </Link>
    )
  }
  if (!item.read) {
    return (
      <button type="button" onClick={markOnInteract} className={innerClassName}>
        {content}
      </button>
    )
  }
  return <div className={innerClassName}>{content}</div>
}

function NotificationActions({
  item,
  onMarkRead,
  onDismiss,
}: NotificationItemCardProps) {
  return (
    <div className="flex shrink-0 items-start gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
      {!item.read && (
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground hover:bg-accent/10 hover:text-accent"
          aria-label="Mark as read"
          title="Mark as read"
          onClick={() => { onMarkRead(item.id) }}
        >
          <Check className="size-3.5" aria-hidden="true" />
        </button>
      )}
      <button
        type="button"
        className="rounded p-0.5 text-muted-foreground hover:bg-danger/10 hover:text-danger"
        aria-label="Dismiss notification"
        title="Dismiss"
        onClick={() => { onDismiss(item.id) }}
      >
        <X className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}

function NotificationItemCardInner({
  item,
  onMarkRead,
  onDismiss,
}: NotificationItemCardProps) {
  const safeHref = _safeInternalHref(item.href)
  const isActionable = !item.read || safeHref !== null
  const innerClassName = cn(
    'flex flex-1 items-start gap-3 text-left -m-px',
    isActionable ? 'cursor-pointer' : 'cursor-default',
  )
  return (
    <div
      role="listitem"
      className={cn(
        'group relative flex w-full gap-3 rounded-md border-l-2 px-3 py-2 text-left',
        // Hover affordance only when the row has somewhere to go: a
        // read-with-no-link item is inert and shouldn't visually
        // suggest interactivity. The per-action icons keep their own
        // hover styles below regardless.
        'transition-colors',
        isActionable && 'hover:bg-card-hover',
        item.read ? 'border-l-transparent' : BORDER_COLORS[item.severity],
        !item.read && 'bg-accent/5',
      )}
    >
      <NotificationMainTarget
        item={item}
        safeHref={safeHref}
        innerClassName={innerClassName}
        onMarkRead={onMarkRead}
      />
      <NotificationActions
        item={item}
        onMarkRead={onMarkRead}
        onDismiss={onDismiss}
      />
    </div>
  )
}

export const NotificationItemCard = memo(NotificationItemCardInner)
