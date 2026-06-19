import { WS_CHANNELS, WS_EVENT_TYPE_VALUES } from '@/api/types/websocket'
import type { WsChannel, WsEvent } from '@/api/types/websocket'

/** Known valid WsChannel values for runtime validation. */
const VALID_WS_CHANNELS: ReadonlySet<string> = new Set(WS_CHANNELS)

/**
 * Module-scoped UTF-8 encoder for byte-length estimation. Lifted out of
 * ``estimateByteLength`` so a hot per-message call does not allocate a fresh
 * ``TextEncoder`` on every frame.
 */
const _encoder = new TextEncoder()

/**
 * Known valid event_type values for runtime validation (defence-in-depth
 * mirror of WS_EVENT_TYPE_VALUES). A future server roll-out that emits an
 * event_type the client does not know about gets dropped here with a
 * structured warning instead of slipping into the dispatch loop. Mirrors
 * the SSE fallback's ``AGUI_EVENT_MAP`` allowlist semantics.
 */
const VALID_WS_EVENT_TYPES: ReadonlySet<string> = new Set(WS_EVENT_TYPE_VALUES)

/** Runtime validation that a parsed message conforms to the WsEvent shape. */
export function isWsEvent(
  msg: Record<string, unknown>,
): msg is Record<string, unknown> & WsEvent {
  return (
    typeof msg['event_type'] === 'string'
    && VALID_WS_EVENT_TYPES.has(msg['event_type'])
    && typeof msg['channel'] === 'string'
    && VALID_WS_CHANNELS.has(msg['channel'])
    && typeof msg['timestamp'] === 'string'
    && typeof msg['payload'] === 'object'
    && msg['payload'] !== null
    && !Array.isArray(msg['payload'])
  )
}

/**
 * Resolve the wire-protocol version of an incoming event. Absent
 * ``version`` is treated as ``1`` so pre-versioning servers still
 * dispatch through the normal handler chain.
 */
export function eventVersion(msg: Record<string, unknown>): number {
  return typeof msg['version'] === 'number' ? msg['version'] : 1
}

/** Validate that a channels array from a server ack contains only known channel strings. */
export function isWsChannelArray(arr: unknown): arr is WsChannel[] {
  return Array.isArray(arr)
    && arr.every((c) => typeof c === 'string' && VALID_WS_CHANNELS.has(c))
}

/** Estimate byte length of a string (accounts for multi-byte characters). */
export function estimateByteLength(str: string): number {
  return _encoder.encode(str).byteLength
}
