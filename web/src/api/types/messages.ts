/** Inter-agent message types and channel metadata.
 *
 * ``Message`` / ``MessageMetadata`` / ``Channel`` are the canonical
 * wire DTOs, re-exported verbatim from the generated ``dtos.gen``
 * module (the wire model uses A2A-style structured ``parts`` --
 * ``TextPart`` / ``DataPart`` / ``FilePart`` / ``UriPart`` -- plus a
 * computed ``text`` accessor). The messaging UI flattens that via
 * the ``messageText`` / ``partsToAttachments`` adapters in
 * ``@/utils/messages``.
 *
 * ``MessageType``, ``MessagePriority`` and ``ChannelType`` are
 * wire-facing enums re-exported from the generated enum module.
 * ``Attachment`` / ``AttachmentType`` are a frontend-only overlay:
 * on the wire, attachments are ``DataPart`` / ``FilePart`` /
 * ``UriPart``; the dashboard adapter classifies them into the
 * simpler ``artifact|file|link`` taxonomy below.
 */

export type {
  Channel,
  Message,
  MessageMetadata,
} from './dtos.gen'

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

export type AttachmentType = 'artifact' | 'file' | 'link'

export const ATTACHMENT_TYPE_VALUES = [
  'artifact',
  'file',
  'link',
] as const satisfies readonly AttachmentType[]

export interface Attachment {
  type: AttachmentType
  ref: string
}
