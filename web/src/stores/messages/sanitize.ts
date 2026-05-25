import { createLogger } from '@/lib/logger'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import { sanitizeForLog } from '@/utils/logging'
import {
  MESSAGE_PRIORITY_VALUES,
  MESSAGE_TYPE_VALUES,
} from '@/api/types/messages'
import type { Message } from '@/api/types/messages'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('messages')

type WireMessagePart = Message['parts'][number]

function isTextPart(p: Record<string, unknown>): boolean {
  return typeof p.text === 'string'
}

function isDataPart(p: Record<string, unknown>): boolean {
  return (
    typeof p.data === 'object'
    && p.data !== null
    && !Array.isArray(p.data)
  )
}

function isFilePart(p: Record<string, unknown>): boolean {
  return (
    typeof p.uri === 'string'
    && (p.mime_type === null || typeof p.mime_type === 'string')
  )
}

function isUriPart(p: Record<string, unknown>): boolean {
  return typeof p.uri === 'string'
}

const PART_TYPE_VALIDATORS: Record<
  string,
  (p: Record<string, unknown>) => boolean
> = {
  text: isTextPart,
  data: isDataPart,
  file: isFilePart,
  uri: isUriPart,
}

/**
 * Each ``parts`` / ``attachments`` entry must be a well-formed
 * structured part (``TextPart`` / ``DataPart`` / ``FilePart`` /
 * ``UriPart``). Without this the per-type sanitizer would dereference
 * a missing ``text`` / ``uri`` / ``data`` and throw. Unknown
 * discriminators are rejected outright (no safe fallback part type).
 */
function isPartsShape(value: unknown): boolean {
  if (!Array.isArray(value)) return false
  return value.every((part) => {
    if (typeof part !== 'object' || part === null || Array.isArray(part)) {
      return false
    }
    const p = part as Record<string, unknown>
    const type = typeof p.type === 'string' ? p.type : ''
    const validator = PART_TYPE_VALIDATORS[type]
    return validator ? validator(p) : false
  })
}

function sanitizeTextPart(part: Record<string, unknown>): WireMessagePart {
  return {
    type: 'text',
    text: sanitizeWsString(part.text as string, 4096) ?? '',
  }
}

function sanitizeFilePart(part: Record<string, unknown>): WireMessagePart {
  return {
    type: 'file',
    uri: sanitizeWsString(part.uri as string, 2048) ?? '',
    mime_type: part.mime_type === null
      ? null
      : sanitizeWsString(part.mime_type as string, 128) ?? '',
  }
}

function sanitizeUriPart(part: Record<string, unknown>): WireMessagePart {
  return {
    type: 'uri',
    uri: sanitizeWsString(part.uri as string, 2048) ?? '',
  }
}

function sanitizeDataPart(part: Record<string, unknown>): WireMessagePart {
  // ``isPartsShape`` already enforced ``data`` is an object literal;
  // it is rendered as data, never interpolated, so passed through.
  return {
    type: 'data',
    data: part.data as Readonly<Record<string, unknown>>,
  }
}

const PART_SANITIZERS: Record<
  string,
  (part: Record<string, unknown>) => WireMessagePart
> = {
  text: sanitizeTextPart,
  file: sanitizeFilePart,
  uri: sanitizeUriPart,
  data: sanitizeDataPart,
}

/**
 * Sanitize one structured part by discriminator. Every untrusted
 * string (``text`` / ``uri`` / ``mime_type``) is clamped via
 * ``sanitizeWsString``; ``DataPart.data`` is structured JSON kept
 * verbatim (it is rendered as data, never interpolated).
 */
function sanitizePart(part: Record<string, unknown>): WireMessagePart {
  const type = typeof part.type === 'string' ? part.type : 'data'
  return (PART_SANITIZERS[type] ?? sanitizeDataPart)(part)
}

function isMetadataNumericFields(m: Record<string, unknown>): boolean {
  // ``Number.isFinite`` rejects ``NaN``/``Infinity``/``-Infinity``. A bare
  // ``typeof === 'number'`` would let those through and poison the store
  // (downstream cost-aggregation / token-sum math silently propagates them).
  return (
    (m.tokens_used === null || Number.isFinite(m.tokens_used))
    && (m.cost === null || Number.isFinite(m.cost))
  )
}

function isMetadataIdFields(m: Record<string, unknown>): boolean {
  return (
    (m.task_id === null || typeof m.task_id === 'string')
    && (m.project_id === null || typeof m.project_id === 'string')
  )
}

function isMetadataExtraField(value: unknown): boolean {
  if (!Array.isArray(value)) return false
  return value.every(
    (entry) =>
      Array.isArray(entry)
      && entry.length === 2
      && typeof entry[0] === 'string'
      && typeof entry[1] === 'string',
  )
}

/**
 * ``MessageMetadata`` carries nullable id pointers, numeric usage
 * fields, and an ``extra`` array of ``[string, string]`` tuples.
 */
function isMessageMetadataShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  const m = value as Record<string, unknown>
  return (
    isMetadataIdFields(m)
    && isMetadataNumericFields(m)
    && isMetadataExtraField(m.extra)
  )
}

const MESSAGE_REQUIRED_STRING_FIELDS = [
  'id',
  'timestamp',
  'sender',
  'to',
  'channel',
  'text',
  'type',
  'priority',
] as const

function isMessageStringFields(c: Record<string, unknown>): boolean {
  for (const field of MESSAGE_REQUIRED_STRING_FIELDS) {
    if (typeof c[field] !== 'string') return false
  }
  return true
}

/**
 * Shallow structural check: every ``Message`` string field is a
 * ``string`` on the wire and ``attachments`` / ``metadata`` carry
 * well-formed nested shapes.
 */
function isMessageShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & Message {
  return (
    isMessageStringFields(c)
    && isPartsShape(c.parts)
    && isPartsShape(c.attachments)
    && isMessageMetadataShape(c.metadata)
  )
}

function sanitizeMessageMetadata(metadata: Message['metadata']) {
  return {
    task_id: metadata.task_id === null
      ? null
      : sanitizeWsString(metadata.task_id, 128) ?? '',
    project_id: metadata.project_id === null
      ? null
      : sanitizeWsString(metadata.project_id, 128) ?? '',
    tokens_used: metadata.tokens_used,
    cost: metadata.cost,
    extra: metadata.extra.map(
      ([k, v]) =>
        [
          sanitizeWsString(k, 64) ?? '',
          sanitizeWsString(v, 512) ?? '',
        ] as [string, string],
    ),
  }
}

interface MessageStringFields {
  id: string
  timestamp: string
  sender: string
  to: string
  channel: string
  text: string
}

function extractMessageStrings(c: Record<string, unknown>): MessageStringFields {
  return {
    id: sanitizeWsString(c.id, 128) ?? '',
    timestamp: sanitizeWsString(c.timestamp, 64) ?? '',
    sender: sanitizeWsString(c.sender) ?? '',
    to: sanitizeWsString(c.to) ?? '',
    channel: sanitizeWsString(c.channel) ?? '',
    text: sanitizeWsString(c.text, 4096) ?? '',
  }
}

function requiredStringsAllNonEmpty(s: MessageStringFields): boolean {
  return Boolean(s.id && s.timestamp && s.sender && s.to && s.channel)
}

function validateAndExtractStrings(
  c: Record<string, unknown>,
): MessageStringFields | null {
  const strings = extractMessageStrings(c)
  if (requiredStringsAllNonEmpty(strings)) return strings
  log.error('WS message blanked by sanitization, skipping', {
    id: sanitizeForLog(c.id),
    hasBlankId: strings.id.length === 0,
    hasBlankTo: strings.to.length === 0,
    hasBlankChannel: strings.channel.length === 0,
  })
  return null
}

/**
 * Validate a WS payload and return a typed Message with every
 * untrusted string field sanitized, or null if malformed.
 */
export function parseWsMessage(
  payload: WsEvent['payload'],
): Message | null {
  if (
    !payload.message
    || typeof payload.message !== 'object'
    || Array.isArray(payload.message)
  ) return null

  const c = payload.message as Record<string, unknown>
  if (!isMessageShape(c)) {
    log.error('Malformed WS payload, skipping', {
      id: sanitizeForLog(c.id),
      hasSender: typeof c.sender === 'string',
      hasChannel: typeof c.channel === 'string',
    })
    return null
  }

  const strings = validateAndExtractStrings(c)
  if (strings === null) return null

  const type = sanitizeWsEnum(c.type, MESSAGE_TYPE_VALUES, 'announcement', {
    maxLen: 64,
    field: 'message.type',
  })
  const priority = sanitizeWsEnum(
    c.priority,
    MESSAGE_PRIORITY_VALUES,
    'normal',
    { maxLen: 64, field: 'message.priority' },
  )

  // ``isPartsShape`` already validated both arrays, so the casts are sound.
  const parts = (c.parts as unknown as Record<string, unknown>[]).map(
    sanitizePart,
  )
  const attachments = (
    c.attachments as unknown as Record<string, unknown>[]
  ).map(sanitizePart)

  return {
    ...strings,
    type,
    priority,
    parts,
    attachments,
    metadata: sanitizeMessageMetadata(c.metadata),
  }
}
