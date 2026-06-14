import { WS_PROTOCOL_MISMATCH_THRESHOLD, WS_PROTOCOL_VERSION } from '@/utils/ws-constants'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type { WsSet } from './types'

const log = createLogger('ws')

let protocolMismatchCount = 0

/** Clear the consecutive-mismatch run counter (on connect / retry). */
export function resetProtocolMismatchCount(): void {
  protocolMismatchCount = 0
}

/**
 * Decide whether an event's wire version is supported, returning true
 * when the caller should dispatch it.
 *
 * A silent drop strands the socket "connected" while no live updates
 * arrive, so on mismatch we log (scrubbing the attacker-reachable
 * ``event_type`` / ``channel`` fields to close the log-injection
 * vector), increment the run counter, and flag the store when the
 * server is ahead of us or a run of mismatches crosses the threshold,
 * so the UI can prompt a reload.
 */
export function isSupportedWireVersion(
  version: number,
  msg: { event_type?: unknown; channel?: unknown },
  set: WsSet,
): boolean {
  if (version === WS_PROTOCOL_VERSION) {
    // A prior transient mismatch may have set the sticky store flag; clear it
    // so the UI unblocks once a supported frame lands. Guard the store write
    // on the counter so the common steady-state frame does not write per-event.
    if (protocolMismatchCount > 0) {
      set({ protocolVersionMismatch: false })
    }
    protocolMismatchCount = 0
    return true
  }
  log.warn('Discarding event with unsupported wire version:', {
    received: version,
    supported: WS_PROTOCOL_VERSION,
    event_type: sanitizeForLog(msg.event_type),
    channel: sanitizeForLog(msg.channel),
  })
  protocolMismatchCount += 1
  if (version > WS_PROTOCOL_VERSION || protocolMismatchCount >= WS_PROTOCOL_MISMATCH_THRESHOLD) {
    set({ protocolVersionMismatch: true })
  }
  return false
}
