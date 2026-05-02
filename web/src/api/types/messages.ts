/** Inter-agent message types and channel metadata. */

export type MessageType =
  | 'task_update'
  | 'question'
  | 'announcement'
  | 'review_request'
  | 'approval'
  | 'delegation'
  | 'status_report'
  | 'escalation'
  | 'meeting_contribution'
  | 'hr_notification'

export type MessagePriority = 'low' | 'normal' | 'high' | 'urgent'

export type AttachmentType = 'artifact' | 'file' | 'link'

export const ATTACHMENT_TYPE_VALUES = [
  'artifact', 'file', 'link',
] as const satisfies readonly AttachmentType[]

export interface Attachment {
  type: AttachmentType
  ref: string
}

export interface MessageMetadata {
  task_id: string | null
  project_id: string | null
  tokens_used: number | null
  cost: number | null
  readonly extra: readonly [string, string][]
}

export interface Message {
  id: string
  timestamp: string
  sender: string
  to: string
  type: MessageType
  priority: MessagePriority
  channel: string
  content: string
  readonly attachments: readonly Attachment[]
  metadata: MessageMetadata
}

export type ChannelType = 'topic' | 'direct' | 'broadcast'

export interface Channel {
  name: string
  type: ChannelType
  readonly subscribers: readonly string[]
}
