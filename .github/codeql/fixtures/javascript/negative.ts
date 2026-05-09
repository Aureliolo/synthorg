// Negative fixtures: each function exercises one modelled barrier.
//
// CodeQL analysis with the synthorg-sanitisers extension pack loaded MUST
// NOT report alerts on these functions. If any rule fires here, the pack
// is under-modelling its sanitiser.

import { sanitizeForLog } from '../../../../web/src/utils/logging'
import { sanitizeWsString, sanitizeWsEnum } from '../../../../web/src/utils/ws-sanitize'

const ALLOWED_LEVELS = ['debug', 'info', 'warn', 'error'] as const
type Level = typeof ALLOWED_LEVELS[number]

export function negativeLogInjection(userInput: unknown): void {
  // js/log-injection MUST NOT fire here.
  console.warn('user said:', sanitizeForLog(userInput))
}

export function negativeWsLogInjection(wsPayload: string): void {
  // js/log-injection MUST NOT fire here. sanitizeWsString clamps and
  // strips controls + bidi overrides.
  console.warn('ws frame:', sanitizeWsString(wsPayload, 200))
}

export function negativeWsEnumLogInjection(wsField: unknown): Level {
  // js/log-injection MUST NOT fire on the returned value -- the enum
  // allowlist guarantees it's a known constant.
  return sanitizeWsEnum<Level>(wsField, ALLOWED_LEVELS, 'info', { field: 'level' })
}
