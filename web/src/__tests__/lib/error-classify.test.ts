import { AxiosError, AxiosHeaders } from 'axios'
import fc from 'fast-check'
import { classifyError } from '@/lib/error-classify'

function axiosErrorWithStatus(status: number): AxiosError {
  const err = new AxiosError('failed', 'ERR', { headers: new AxiosHeaders() }, null, {
    status,
    statusText: 'err',
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
    data: {},
  })
  return err
}

describe('classifyError', () => {
  it('network error (no response) is transient + retryable', () => {
    const err = new AxiosError('network fail')
    delete err.response
    const c = classifyError(err)
    expect(c.isTransient).toBe(true)
    expect(c.isClient).toBe(false)
    expect(c.retryable).toBe(true)
    expect(c.guidance).toMatch(/network connection/i)
  })

  it('5xx is transient + retryable', () => {
    const c = classifyError(axiosErrorWithStatus(503))
    expect(c.status).toBe(503)
    expect(c.isTransient).toBe(true)
    expect(c.retryable).toBe(true)
  })

  it('408 Request Timeout is transient + retryable', () => {
    const c = classifyError(axiosErrorWithStatus(408))
    expect(c.isTransient).toBe(true)
    expect(c.retryable).toBe(true)
  })

  it('429 Rate Limited is transient + retryable with guidance', () => {
    const c = classifyError(axiosErrorWithStatus(429))
    expect(c.isTransient).toBe(true)
    expect(c.retryable).toBe(true)
    expect(c.guidance).toMatch(/rate limited/i)
  })

  it('401 is client + not retryable with guidance', () => {
    const c = classifyError(axiosErrorWithStatus(401))
    expect(c.isClient).toBe(true)
    expect(c.retryable).toBe(false)
    expect(c.guidance).toMatch(/sign in/i)
  })

  it('403 is client + not retryable with guidance', () => {
    const c = classifyError(axiosErrorWithStatus(403))
    expect(c.isClient).toBe(true)
    expect(c.retryable).toBe(false)
    expect(c.guidance).toMatch(/permission/i)
  })

  it('404 is client + not retryable', () => {
    const c = classifyError(axiosErrorWithStatus(404))
    expect(c.isClient).toBe(true)
    expect(c.retryable).toBe(false)
  })

  it('409 is client and non-retryable (conflict needs user action)', () => {
    const c = classifyError(axiosErrorWithStatus(409))
    expect(c.isClient).toBe(true)
    expect(c.retryable).toBe(false)
    expect(c.guidance).toMatch(/refresh/i)
  })

  it('canceled axios request is non-retryable and not transient', () => {
    const err = new AxiosError('canceled', 'ERR_CANCELED')
    const c = classifyError(err)
    expect(c.isTransient).toBe(false)
    expect(c.isClient).toBe(false)
    expect(c.retryable).toBe(false)
    expect(c.guidance).toMatch(/canceled/i)
  })

  it('422 (other 4xx) is client + not retryable', () => {
    const c = classifyError(axiosErrorWithStatus(422))
    expect(c.isClient).toBe(true)
    expect(c.retryable).toBe(false)
  })

  it('non-axios TypeError is neither transient nor client', () => {
    const c = classifyError(new TypeError('boom'))
    expect(c.isTransient).toBe(false)
    expect(c.isClient).toBe(false)
    expect(c.retryable).toBe(false)
  })

  it('surfaces a message', () => {
    const c = classifyError(new Error('bang'))
    expect(c.message).toBe('bang')
  })

  it('property: every 5xx status is transient + retryable', () => {
    fc.assert(
      fc.property(fc.integer({ min: 500, max: 599 }), (status) => {
        const c = classifyError(axiosErrorWithStatus(status))
        expect(c.isTransient).toBe(true)
        expect(c.isClient).toBe(false)
        expect(c.retryable).toBe(true)
      }),
      { numRuns: 20 },
    )
  })

  it('property: 4xx statuses (excluding 408/429/409) are client + non-retryable', () => {
    fc.assert(
      fc.property(fc.integer({ min: 400, max: 499 }), (status) => {
        fc.pre(status !== 408 && status !== 429 && status !== 409)
        const c = classifyError(axiosErrorWithStatus(status))
        expect(c.isClient).toBe(true)
        expect(c.isTransient).toBe(false)
        expect(c.retryable).toBe(false)
      }),
      { numRuns: 20 },
    )
  })
})
