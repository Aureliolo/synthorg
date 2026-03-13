/** Sanitize a value for safe logging (strip control chars, truncate). */
export function sanitizeForLog(value: unknown, maxLen = 500): string {
  const raw = value instanceof Error ? value.message : String(value)
  let result = ''
  for (const ch of raw) {
    const code = ch.charCodeAt(0)
    result += (code >= 0x20 && code !== 0x7f) ? ch : ' '
    if (result.length >= maxLen) break
  }
  return result
}
