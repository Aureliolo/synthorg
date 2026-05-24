const DEFAULT_MAX_LEN = 500

/** Unicode BIDI override and direction control ranges that can manipulate log display. */
function isBidiControl(code: number): boolean {
  return (code >= 0x200b && code <= 0x200f)
    || (code >= 0x202a && code <= 0x202e)
    || (code >= 0x2066 && code <= 0x2069)
    || (code >= 0xfff9 && code <= 0xfffb)
}

/** True for ASCII control characters and the C1 control block. */
function _isAsciiControl(code: number): boolean {
  return code < 0x20 || code === 0x7f || (code >= 0x80 && code <= 0x9f)
}

/** Clamp the caller-supplied cap, falling back to the default. */
function _coerceCap(maxLen: number): number {
  if (!Number.isFinite(maxLen)) return DEFAULT_MAX_LEN
  return Math.max(0, Math.floor(maxLen))
}

/**
 * Coerce any value into a string suitable for sanitisation. Errors get
 * their stack (or message) so the call site sees the trace; everything
 * else gets a defensive `String()` with a fallback for objects whose
 * `toString` throws.
 */
function _extractRawString(value: unknown): string {
  if (value instanceof Error) {
    return value.stack ?? value.message ?? String(value)
  }
  try {
    return String(value)
  } catch {
    return '[unstringifiable]'
  }
}

/** Sanitize a value for safe logging (strip control chars + BIDI overrides, truncate). */
export function sanitizeForLog(value: unknown, maxLen = DEFAULT_MAX_LEN): string {
  const cap = _coerceCap(maxLen)
  if (cap === 0) return ''
  const raw = _extractRawString(value)
  let result = ''
  for (const ch of raw) {
    const code = ch.codePointAt(0) ?? 0
    result += (!_isAsciiControl(code) && !isBidiControl(code)) ? ch : ' '
    if (result.length >= cap) break
  }
  return result
}
