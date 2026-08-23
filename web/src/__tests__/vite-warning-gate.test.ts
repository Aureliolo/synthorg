import { describe, expect, it, vi } from 'vitest'

import { NON_BLOCKING_LOG_CODES, failBuildOnWarning } from '../../vite-warning-gate'

/**
 * The gate turns bundler warnings into build failures. Its whole value is
 * that it fires, and a warning is exactly the signal nobody notices when it
 * stops: the build stays green either way.
 */
describe('failBuildOnWarning', () => {
  it('throws on a warning that carries no exempt code', () => {
    const defaultHandler = vi.fn()

    expect(() =>
      failBuildOnWarning('warn', { code: 'NAMESPACE_CONFLICT', message: 'clash' }, defaultHandler),
    ).toThrow(/NAMESPACE_CONFLICT/)
    expect(defaultHandler).not.toHaveBeenCalled()
  })

  it('throws on a warning with no code at all', () => {
    const defaultHandler = vi.fn()

    expect(() => failBuildOnWarning('warn', { message: 'anonymous' }, defaultHandler)).toThrow(
      /UNCODED_WARNING/,
    )
  })

  it.each([...NON_BLOCKING_LOG_CODES])('forwards the exempt code %s instead of throwing', (code) => {
    const defaultHandler = vi.fn()

    failBuildOnWarning('warn', { code, message: 'exempt' }, defaultHandler)

    expect(defaultHandler).toHaveBeenCalledOnce()
  })

  it.each(['info', 'debug'] as const)('forwards %s untouched', (level) => {
    const defaultHandler = vi.fn()

    failBuildOnWarning(level, { code: 'NAMESPACE_CONFLICT', message: 'not a warning' }, defaultHandler)

    expect(defaultHandler).toHaveBeenCalledOnce()
  })

  it('exempts exactly the codes Vite drops in its own default handler', () => {
    expect([...NON_BLOCKING_LOG_CODES].sort()).toEqual([
      'CIRCULAR_DEPENDENCY',
      'THIS_IS_UNDEFINED',
    ])
  })
})
