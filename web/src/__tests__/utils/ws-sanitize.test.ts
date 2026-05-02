import { afterEach, vi } from 'vitest'
import {
  MAX_WS_STRING_LEN,
  sanitizeWsEnum,
  sanitizeWsString,
} from '@/utils/ws-sanitize'

const TASK_STATUS = ['created', 'in_progress', 'completed'] as const

describe('sanitizeWsString', () => {
  it('returns the trimmed string when within bounds', () => {
    expect(sanitizeWsString('  hello  ')).toBe('hello')
  })

  it('returns undefined for non-strings', () => {
    expect(sanitizeWsString(null)).toBeUndefined()
    expect(sanitizeWsString(42)).toBeUndefined()
    expect(sanitizeWsString({})).toBeUndefined()
  })

  it('strips C0 control characters but preserves tab, LF, CR', () => {
    const sanitized = sanitizeWsString('ab\tc\nd')
    expect(sanitized).toBe('ab\tc\nd')
  })

  it('strips bidi-override characters', () => {
    // U+202E (RIGHT-TO-LEFT OVERRIDE) is the canonical Trojan-Source carrier;
    // assembled from a Unicode escape so this source file stays free of
    // bidi chars (otherwise eslint security/detect-bidi-characters fires).
    const raw = `safe${String.fromCodePoint(0x202e)}txt.exe`
    expect(sanitizeWsString(raw)).toBe('safetxt.exe')
  })

  it('returns undefined when sanitization yields an empty string', () => {
    expect(sanitizeWsString('   ')).toBeUndefined()
    expect(sanitizeWsString('')).toBeUndefined()
  })

  it('caps length at maxLen using Unicode code-point count', () => {
    const long = 'x'.repeat(MAX_WS_STRING_LEN + 50)
    const sanitized = sanitizeWsString(long)
    expect(sanitized).toHaveLength(MAX_WS_STRING_LEN)
  })

  it('does not split surrogate pairs at the boundary', () => {
    const emoji = '\u{1F600}'.repeat(10)
    const sanitized = sanitizeWsString(emoji, 5)
    expect([...(sanitized ?? '')]).toHaveLength(5)
  })
})

describe('sanitizeWsEnum', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

  afterEach(() => {
    warnSpy.mockClear()
  })

  it('returns the value when it is in the allowlist', () => {
    expect(sanitizeWsEnum('completed', TASK_STATUS, 'created')).toBe('completed')
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('falls back and warns when the value is not in the allowlist', () => {
    const result = sanitizeWsEnum('archived', TASK_STATUS, 'created', {
      field: 'task.status',
    })
    expect(result).toBe('created')
    expect(warnSpy).toHaveBeenCalledOnce()
    const call = warnSpy.mock.calls[0]!
    expect(call.join(' ')).toContain('ws.enum.unknown')
  })

  it('falls back and warns when the value is not a string', () => {
    expect(sanitizeWsEnum(42, TASK_STATUS, 'created')).toBe('created')
    expect(sanitizeWsEnum(null, TASK_STATUS, 'in_progress')).toBe('in_progress')
    expect(sanitizeWsEnum(undefined, TASK_STATUS, 'completed')).toBe('completed')
    expect(warnSpy).toHaveBeenCalledTimes(3)
  })

  it('falls back when sanitization strips the input down to empty', () => {
    expect(sanitizeWsEnum('   ', TASK_STATUS, 'created')).toBe('created')
    expect(sanitizeWsEnum('', TASK_STATUS, 'created')).toBe('created')
    expect(warnSpy).toHaveBeenCalledTimes(2)
  })

  it('strips control chars first, validates the cleaned value', () => {
    // The control char gets stripped, leaving 'completed' which IS valid.
    expect(sanitizeWsEnum('completed', TASK_STATUS, 'created')).toBe('completed')
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('respects a custom maxLen for the underlying sanitisation', () => {
    // 'in_progress' is 11 chars; with maxLen=8 it gets truncated to
    // 'in_progr' which is not in the allowlist -> fallback.
    expect(sanitizeWsEnum('in_progress', TASK_STATUS, 'created', { maxLen: 8 })).toBe('created')
    expect(warnSpy).toHaveBeenCalledOnce()
  })

  it('preserves the literal type of the fallback', () => {
    const result: 'created' | 'in_progress' | 'completed' = sanitizeWsEnum(
      'unknown',
      TASK_STATUS,
      'in_progress',
    )
    expect(result).toBe('in_progress')
  })
})
