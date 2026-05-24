import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import type { WsEvent } from '@/api/types/websocket'
import { pendingTransitions } from './_state'
import { isApprovalShape, sanitizeApproval } from './sanitize'
import type { ApprovalsGet } from './types'

const log = createLogger('approvals')

export function createWsHandler(get: ApprovalsGet) {
  return {
    handleWsEvent(event: WsEvent): void {
      // Guard the envelope first: ``event.payload`` is typed as
      // ``Record<string, unknown>`` on the wire but a malformed broker
      // could still send ``null`` or a non-object, in which case
      // reading ``.approval`` off it would throw. Drop those frames
      // silently rather than letting the WS pipeline crash.
      if (!isObject(event.payload)) return
      const payload = event.payload
      // ``isObject`` narrows in one step instead of the inline hand-rolled
      // ``typeof === 'object' && !== null && !Array.isArray`` chain
      // followed by a downstream ``as`` cast.
      if (!isObject(payload.approval)) return
      const candidate: Record<string, unknown> = payload.approval
      if (!isApprovalShape(candidate)) {
        log.error('Received malformed approval payload, skipping upsert', {
          id: sanitizeForLog(candidate.id),
          hasTitle: typeof candidate.title === 'string',
          hasStatus: typeof candidate.status === 'string',
        })
        return
      }
      // Sanitize *before* the pendingTransitions check so a frame
      // whose id carries control/bidi chars can't bypass the
      // optimistic-transition gate (which keys off the raw id) and
      // then sanitize to the plain id to overwrite a real approval.
      // Mutation = the wire id carried chars we stripped, so we
      // can't trust it to point at the intended record.
      const sanitized = sanitizeApproval(candidate)
      if (!sanitized.id || sanitized.id !== candidate.id) {
        log.error(
          'Approval payload lost or mutated id during sanitization, skipping upsert',
          sanitizeForLog({
            raw_id: candidate.id,
            sanitized_id: sanitized.id,
          }),
        )
        return
      }
      if (pendingTransitions.has(sanitized.id)) return
      get().upsertApproval(sanitized)
    },
  }
}
