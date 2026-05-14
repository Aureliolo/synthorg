import type { ChannelType, Message, MessagePriority, MessageType } from '@/api/types/messages'
import type { SemanticColor } from '@/lib/utils'
import { formatDateOnly } from '@/utils/format'

// ── Message type labels ────────────────────────────────────

const MESSAGE_TYPE_LABELS: Record<MessageType, string> = {
  task_update: 'Task Update',
  question: 'Question',
  announcement: 'Announcement',
  review_request: 'Review Request',
  approval: 'Approval',
  delegation: 'Delegation',
  status_report: 'Status Report',
  escalation: 'Escalation',
  meeting_contribution: 'Meeting',
  hr_notification: 'HR Notice',
  dissent: 'Dissent',
  context_injection: 'Context',
}

export function getMessageTypeLabel(type: MessageType): string {
  return MESSAGE_TYPE_LABELS[type]
}

// ── Priority color mapping ─────────────────────────────────

const PRIORITY_COLOR_MAP: Partial<Record<MessagePriority, SemanticColor>> = {
  high: 'warning',
  urgent: 'danger',
}

/** Returns a semantic color for high/urgent priorities, null for low/normal. */
export function getMessagePriorityColor(priority: MessagePriority): SemanticColor | null {
  return PRIORITY_COLOR_MAP[priority] ?? null
}

// Static class maps for Tailwind (dynamic interpolation gets purged in production)
const PRIORITY_DOT_CLASSES: Record<SemanticColor, string> = {
  warning: 'bg-warning',
  danger: 'bg-danger',
  success: 'bg-success',
  accent: 'bg-accent',
}

export function getPriorityDotClass(color: SemanticColor): string {
  return PRIORITY_DOT_CLASSES[color]
}

const PRIORITY_BADGE_CLASSES: Record<SemanticColor, string> = {
  warning: 'border-warning/30 bg-warning/10 text-warning',
  danger: 'border-danger/30 bg-danger/10 text-danger',
  success: 'border-success/30 bg-success/10 text-success',
  accent: 'border-accent/30 bg-accent/10 text-accent',
}

export function getPriorityBadgeClasses(color: SemanticColor): string {
  return PRIORITY_BADGE_CLASSES[color]
}

// ── Channel type labels ────────────────────────────────────

const CHANNEL_TYPE_LABELS: Record<ChannelType, string> = {
  topic: 'Topics',
  direct: 'Direct',
  broadcast: 'Broadcast',
}

export function getChannelTypeLabel(type: ChannelType): string {
  return CHANNEL_TYPE_LABELS[type]
}

// ── Date grouping ──────────────────────────────────────────

/** Extract the YYYY-MM-DD date key from an ISO timestamp. */
function toDateKey(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'unknown'
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-')
}

/**
 * Group messages by date key (YYYY-MM-DD), preserving order within each group.
 * Returns a Map with keys in chronological order.
 */
export function groupMessagesByDate(messages: readonly Message[]): Map<string, Message[]> {
  const groups = new Map<string, Message[]>()
  for (const msg of messages) {
    const key = toDateKey(msg.timestamp)
    const bucket = groups.get(key)
    if (bucket) {
      bucket.push(msg)
    } else {
      groups.set(key, [msg])
    }
  }
  return groups
}

/**
 * Get a human-readable label for a date key.
 * Returns "Today", "Yesterday", or a formatted date string.
 */
export function getDateGroupLabel(dateKey: string): string {
  if (dateKey === 'unknown') return 'Unknown'
  const [year, month, day] = dateKey.split('-').map(Number) as [number, number, number]
  const target = new Date(year, month - 1, day)
  if (Number.isNaN(target.getTime())) return dateKey

  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  if (target.getTime() === today.getTime()) return 'Today'
  if (target.getTime() === yesterday.getTime()) return 'Yesterday'

  return formatDateOnly(target)
}

// ── Thread grouping ────────────────────────────────────────

export interface ThreadGroup {
  readonly threads: ReadonlyMap<string, readonly Message[]>
  readonly standalone: readonly Message[]
}

/**
 * Group messages by thread (metadata.task_id).
 * Messages with null/undefined task_id are standalone.
 */
export function groupMessagesByThread(messages: readonly Message[]): ThreadGroup {
  const threads = new Map<string, Message[]>()
  const standalone: Message[] = []

  for (const msg of messages) {
    const taskId = msg.metadata.task_id
    if (!taskId) {
      standalone.push(msg)
      continue
    }
    const bucket = threads.get(taskId)
    if (bucket) {
      bucket.push(msg)
    } else {
      threads.set(taskId, [msg])
    }
  }

  return { threads, standalone }
}

// ── Client-side filtering ──────────────────────────────────

export interface MessagePageFilters {
  type?: MessageType
  priority?: MessagePriority
  search?: string
}

export function filterMessages(
  messages: readonly Message[],
  filters: MessagePageFilters,
): Message[] {
  let result = [...messages]

  if (filters.type) {
    result = result.filter((m) => m.type === filters.type)
  }

  if (filters.priority) {
    result = result.filter((m) => m.priority === filters.priority)
  }

  if (filters.search) {
    const query = filters.search.toLowerCase()
    result = result.filter(
      (m) =>
        m.content.toLowerCase().includes(query) ||
        m.sender.toLowerCase().includes(query) ||
        m.to.toLowerCase().includes(query),
    )
  }

  return result
}
