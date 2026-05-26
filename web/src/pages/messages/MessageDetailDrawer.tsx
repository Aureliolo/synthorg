import { Drawer } from '@/components/ui/drawer'
import { Avatar } from '@/components/ui/avatar'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatDateTime, formatCurrency } from '@/utils/format'
import { MessageTypeBadge } from './MessageTypeBadge'
import { AttachmentList } from './AttachmentList'
import {
  getMessagePriorityColor,
  getPriorityDotClass,
  getPriorityBadgeClasses,
  messageText,
  partsToAttachments,
} from '@/utils/messages'
import { cn } from '@/lib/utils'
import type { Message } from '@/api/types/messages'

interface MessageDetailDrawerProps {
  message: Message | null
  open: boolean
  onClose: () => void
}

export function MessageDetailDrawer({ message, open, onClose }: MessageDetailDrawerProps) {
  return (
    <Drawer open={open} onClose={onClose} title={message?.sender ?? 'Message'}>
      {message && <MessageDetailContent message={message} />}
    </Drawer>
  )
}

interface MessageDetailContentProps {
  message: Message
}

function MessageDetailContent({ message }: MessageDetailContentProps) {
  const priorityColor = getMessagePriorityColor(message.priority)
  const messageAttachments = partsToAttachments(message.parts)

  return (
    <div className="space-y-section-gap">
      {/* Sender header */}
      <div className="flex items-center gap-3">
        <Avatar name={message.sender} size="lg" />
        <div>
          <div className="font-mono text-sm font-medium text-foreground">{message.sender}</div>
          <div className="font-mono text-xs text-muted-foreground">to {message.to}</div>
        </div>
      </div>

      {/* Badges */}
      <div className="flex flex-wrap items-center gap-2">
        <MessageTypeBadge type={message.type} />
        {priorityColor && (
          <span className={cn(
            'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-micro',
            getPriorityBadgeClasses(priorityColor),
          )}>
            <span className={cn('size-1.5 rounded-full', getPriorityDotClass(priorityColor))} />
            {message.priority.charAt(0).toUpperCase() + message.priority.slice(1)}
          </span>
        )}
      </div>

      {/* Content */}
      <div>
        <h3 className="mb-1 text-micro font-semibold uppercase tracking-wider text-muted-foreground">Content</h3>
        <p className="whitespace-pre-wrap text-sm text-foreground">
          {messageText(message)}
        </p>
      </div>

      {/* Metadata */}
      <div>
        <h3 className="mb-2 text-micro font-semibold uppercase tracking-wider text-muted-foreground">Details</h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
          <MetadataRow label="Channel" value={message.channel} mono />
          <MetadataRow label="Timestamp" value={formatDateTime(message.timestamp)} mono />
          {message.metadata.task_id && (
            <MetadataRow label="Task ID" value={message.metadata.task_id} mono />
          )}
          {message.metadata.project_id && (
            <MetadataRow label="Project ID" value={message.metadata.project_id} mono />
          )}
          {message.metadata.tokens_used !== null && (
            <MetadataRow label="Tokens" value={String(message.metadata.tokens_used)} mono />
          )}
          {message.metadata.cost !== null && (
            <MetadataRow label="Cost" value={formatCurrency(message.metadata.cost, DEFAULT_CURRENCY)} mono />
          )}
          {message.metadata.extra.map(([key, value], i) => (
            <MetadataRow
              // eslint-disable-next-line @eslint-react/no-array-index-key -- extra pairs lack stable IDs
              key={`extra-${i}-${key}`}
              label={key}
              value={value}
              mono
            />
          ))}
        </dl>
      </div>

      {/* Attachments */}
      {messageAttachments.length > 0 && (
        <div>
          <h3 className="mb-2 text-micro font-semibold uppercase tracking-wider text-muted-foreground">Attachments</h3>
          <AttachmentList attachments={messageAttachments} />
        </div>
      )}
    </div>
  )
}

interface MetadataRowProps {
  label: string
  value: string
  mono?: boolean
}

function MetadataRow({ label, value, mono }: MetadataRowProps) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn('text-foreground', mono && 'font-mono')}>{value}</dd>
    </>
  )
}
