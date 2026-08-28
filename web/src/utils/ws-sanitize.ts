/** Pure helper for clamping WebSocket-supplied strings.
 *
 * Lives outside `@/stores/notifications` so that benchmark + unit
 * test imports can pull in the helper without dragging the store
 * module's side effects (the toast queue, and the backend hydration
 * its preferences perform) into the import graph. The store
 * re-exports `sanitizeWsString` from this module so existing
 * call sites keep working unchanged.
 */

import { createLogger } from '@/lib/logger'

const log = createLogger('ws-sanitize')

export const MAX_WS_STRING_LEN = 128

// Regex character classes are built via ``new RegExp`` from
// escape-sequence strings rather than written as inline regex
// literals so the source file contains zero bidi characters
// (ESLint's ``security/detect-bidi-characters`` would flag a
// literal regex containing U+202E etc.). Runtime behaviour matches
// the equivalent inline character-class literal exactly.
const C0_AND_DEL_PATTERN = new RegExp(
  // eslint-disable-next-line no-control-regex
  '[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F]',
  'g',
)
const BIDI_OVERRIDE_PATTERN = new RegExp(
  '[\\u202A-\\u202E\\u2066-\\u2069]',
  'g',
)

/**
 * Clamp a WS-supplied string for safe storage and display.
 *
 * React escapes HTML at render time, so XSS via the default text path is
 * already covered. This helper adds defense-in-depth for the
 * non-presentational paths:
 *  - strips C0 controls and DELETE (U+0000..U+001F, U+007F) that would
 *    corrupt log lines, terminal output, and notification tooltips,
 *    except the common whitespace set (TAB U+0009, LF U+000A, CR U+000D)
 *    so multi-line messages retain their line structure,
 *  - strips bidi-override characters (U+202A..U+202E, U+2066..U+2069)
 *    that can flip on-screen token order in sensitive UI
 *    (CVE-2021-42574 / Trojan Source class of attacks),
 *  - trims surrounding whitespace and caps length at `maxLen`. The length
 *    cap iterates by Unicode code points so surrogate pairs (emojis, rare
 *    CJK) are never split mid-character.
 *
 * Returns `undefined` for non-strings so callers can pass the result
 * directly into an optional field without widening the type.
 */
export function sanitizeWsString(
  value: unknown,
  maxLen: number = MAX_WS_STRING_LEN,
): string | undefined {
  if (typeof value !== 'string') return undefined
  const stripped = value
    .replace(C0_AND_DEL_PATTERN, '')
    .replace(BIDI_OVERRIDE_PATTERN, '')
    .trim()
  if (stripped.length === 0) return undefined
  const codePoints = Array.from(stripped)
  if (codePoints.length <= maxLen) return stripped
  return codePoints.slice(0, maxLen).join('')
}

/**
 * Sanitize a WS-supplied string and validate it against an enum
 * allowlist. On unknown values (the server emits a value the client
 * does not yet know, or sanitization strips the input down to empty),
 * emit a structured `ws.enum.unknown` warning and return the supplied
 * fallback so the UI can keep rendering.
 *
 * Forward-compatible by design: rolling backend deploys can ship a new
 * enum value before the frontend learns about it; the warning surfaces
 * the drift via observability without breaking the user experience.
 *
 * The fallback type parameter is constrained by `T` so call sites can
 * only pick a value that already exists in the allowlist, keeping
 * downstream code statically typed.
 *
 * The `field` option is mandatory so the warning carries enough
 * diagnostic context to identify which field drifted; multiple
 * unknown-enum hits in the same payload would otherwise be
 * indistinguishable in observability.
 */
export function sanitizeWsEnum<T extends string>(
  value: unknown,
  allowlist: readonly T[],
  fallback: T,
  options: { field: string; maxLen?: number },
): T {
  const sanitized = sanitizeWsString(value, options.maxLen ?? MAX_WS_STRING_LEN)
  if (sanitized !== undefined && (allowlist as readonly string[]).includes(sanitized)) {
    return sanitized as T
  }
  log.warn('ws.enum.unknown', {
    field: options.field,
    raw: sanitized ?? null,
    fallback,
  })
  return fallback
}

/**
 * Strict enum variant: returns `null` on an unknown / blank value instead of a
 * fallback. Use where defaulting would be unsafe -- e.g. a malformed run
 * outcome must never be silently presented as `succeeded`, and a nested ref
 * with an invalid status/type is rejected rather than displayed with a
 * fabricated value.
 */
export function sanitizeWsEnumOrNull<T extends string>(
  value: unknown,
  allowlist: readonly T[],
  options: { field: string; maxLen?: number },
): T | null {
  const sanitized = sanitizeWsString(value, options.maxLen ?? MAX_WS_STRING_LEN)
  if (sanitized !== undefined && (allowlist as readonly string[]).includes(sanitized)) {
    return sanitized as T
  }
  log.warn('ws.enum.unknown', { field: options.field, raw: sanitized ?? null, fallback: null })
  return null
}
