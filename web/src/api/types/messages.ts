/** Inter-agent message types and channel metadata.
 *
 * Note: the dashboard's ``Message`` shape is a frontend view that
 * does not mirror the OpenAPI ``components.schemas.Message``
 * directly. The wire model uses A2A-style structured ``parts``
 * (``TextPart`` / ``DataPart`` / ``FilePart`` / ``UriPart``); the
 * dashboard's adapters flatten that into the simpler shape below
 * for the messaging UI. ``MessageType``, ``MessagePriority`` and
 * ``ChannelType`` are wire-facing enums and are re-exported from
 * the generated module; ``AttachmentType`` is frontend-only
 * because attachments are encoded as ``DataPart`` / ``FilePart``
 * / ``UriPart`` on the wire and the dashboard adapter classifies
 * them into the simpler ``artifact|file|link`` taxonomy below.
 */

export type {
  ChannelType,
  MessagePriority,
  MessageType,
} from './enum-values.gen'

export {
  CHANNEL_TYPE_VALUES,
  MESSAGE_PRIORITY_VALUES,
  MESSAGE_TYPE_VALUES,
} from './enum-values.gen'

import type {
  ChannelType,
  MessagePriority,
  MessageType,
} from './enum-values.gen'

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

export interface Channel {
  name: string
  type: ChannelType
  readonly subscribers: readonly string[]
}
