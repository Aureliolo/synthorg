import type { MockInstance } from 'vitest'
import { createLogger } from '@/lib/logger'

describe('createLogger', () => {
  let warnSpy: MockInstance<typeof console.warn>
  let errorSpy: MockInstance<typeof console.error>

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
    errorSpy.mockRestore()
  })

  it('prefixes warn messages with module name', () => {
    const log = createLogger('test-module')
    log.warn('something happened')
    expect(warnSpy).toHaveBeenCalledWith('[test-module]', 'something happened')
  })

  it('prefixes error messages with module name', () => {
    const log = createLogger('ws')
    log.error('connection lost')
    expect(errorSpy).toHaveBeenCalledWith('[ws]', 'connection lost')
  })

  it('sanitizes string arguments', () => {
    const log = createLogger('m')
    const malicious = 'safe\x00text'
    log.warn('msg', malicious)
    // sanitizeForLog replaces control chars with spaces
    expect(warnSpy).toHaveBeenCalledWith('[m]', 'msg', 'safe text')
  })

  it('sanitizes Error arguments', () => {
    const log = createLogger('m')
    const err = new Error('boom')
    log.error('failed', err)
    const sanitized: unknown = errorSpy.mock.calls[0]?.[2]
    expect(typeof sanitized).toBe('string')
    expect(sanitized).toContain('boom')
  })

  it('passes objects through unchanged', () => {
    const log = createLogger('m')
    const ctx = { key: 'value', count: 42 }
    log.warn('context', ctx)
    expect(warnSpy).toHaveBeenCalledWith('[m]', 'context', ctx)
  })

  it('sanitizes number primitives to strings', () => {
    const log = createLogger('m')
    log.error('code', 404)
    expect(errorSpy).toHaveBeenCalledWith('[m]', 'code', '404')
  })

  it('handles multiple arguments', () => {
    const log = createLogger('multi')
    log.warn('a', 'b', { c: 1 })
    expect(warnSpy).toHaveBeenCalledWith('[multi]', 'a', 'b', { c: 1 })
  })

  it('sanitizes null to string "null"', () => {
    const log = createLogger('m')
    log.warn('val', null)
    expect(warnSpy).toHaveBeenCalledWith('[m]', 'val', 'null')
  })

  it('sanitizes undefined to string "undefined"', () => {
    const log = createLogger('m')
    log.warn('val', undefined)
    expect(warnSpy).toHaveBeenCalledWith('[m]', 'val', 'undefined')
  })

  it('emits debug only in DEV mode', () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
    const log = createLogger('m')
    log.debug('test')
    // In test env, import.meta.env.DEV is true
    expect(debugSpy).toHaveBeenCalledWith('[m]', 'test')
    debugSpy.mockRestore()
  })
})
