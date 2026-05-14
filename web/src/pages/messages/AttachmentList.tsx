import { Link, Package, Paperclip } from 'lucide-react'
import type { Attachment, AttachmentType } from '@/api/types/messages'

const ATTACHMENT_ICONS: Record<AttachmentType, typeof Paperclip> = {
  file: Paperclip,
  link: Link,
  artifact: Package,
}

interface AttachmentListProps {
  attachments: readonly Attachment[]
}

export function AttachmentList({ attachments }: AttachmentListProps) {
  if (attachments.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2">
      {attachments.map((att, i) => {
        const Icon = ATTACHMENT_ICONS[att.type]
        return (
          <span
            // eslint-disable-next-line @eslint-react/no-array-index-key -- attachments lack stable IDs
            key={`${att.type}-${att.ref}-${i}`}
            className="inline-flex items-center gap-1 rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-secondary"
          >
            <Icon className="size-3" aria-hidden="true" />
            {att.ref}
          </span>
        )
      })}
    </div>
  )
}
