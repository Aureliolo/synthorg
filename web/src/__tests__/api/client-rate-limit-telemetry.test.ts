/**
 * 429 retry interceptor emits structured ``http.rate_limited`` log.
 *
 * Every 429-triggered retry must surface a structured
 * ``log.warn('http.rate_limited', ...)`` event so dashboards and operators
 * can track client-side rate-limit pressure instead of having the retries
 * absorbed silently.
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { vi } from 'vitest'

vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: false }))

import { apiClient } from '@/api/client'

function _build429Error(
  method: string,
  headers: Record<string, string> = {},
): AxiosError {
  const config = {
    method,
    headers,
    _rateLimitRetries: 0,
  } as unknown as InternalAxiosRequestConfig
  return new AxiosError(
    'Too Many Requests',
    'ERR_BAD_RESPONSE',
    config,
    undefined,
    {
      status: 429,
      data: {},
      headers: { 'retry-after': '0' },
      statusText: 'Too Many Requests',
      config,
    } as AxiosResponse,
  )
}

/**
 * Extract the structured payloads of every ``http.rate_limited`` log call.
 *
 * Filters the spy's call history by the logger prefix and event name, then
 * returns the payload object. This keeps the test focused on behaviour (the
 * payload shape) rather than the spy's argument positions; if the logger's
 * call signature changes, only this helper needs to adapt.
 */
function _rateLimitedPayloads(
  warnSpy: ReturnType<typeof vi.spyOn>,
): Array<Record<string, unknown>> {
  return warnSpy.mock.calls
    .filter(
      (call: unknown[]) =>
        call[0] === '[api-client]' && call[1] === 'http.rate_limited',
    )
    .map((call: unknown[]) => call[2] as Record<string, unknown>)
}

describe('apiClient 429 retry telemetry', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>
  let requestSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    requestSpy = vi.spyOn(apiClient, 'request').mockResolvedValue({
      status: 200,
      data: { success: true, data: null },
      headers: {},
      statusText: 'OK',
      config: {} as AxiosResponse['config'],
    })
  })

  afterEach(() => {
    warnSpy.mockRestore()
    requestSpy.mockRestore()
  })

  it('emits http.rate_limited on the first 429 retry for a GET', async () => {
    const error = _build429Error('get')
    await apiClient.interceptors.response.handlers?.[0]?.rejected?.(error)

    const payloads = _rateLimitedPayloads(warnSpy)
    expect(payloads).toHaveLength(1)
    expect(payloads[0]).toMatchObject({
      retry_count: 1,
      method: 'get',
      status: 429,
    })
  })

  it('does NOT emit http.rate_limited on a POST without idempotency-key', async () => {
    const error = _build429Error('post')
    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(error),
    ).rejects.toBeDefined()

    expect(_rateLimitedPayloads(warnSpy)).toHaveLength(0)
  })

  it('retries POST with non-empty Idempotency-Key and emits the log', async () => {
    const error = _build429Error('post', { 'idempotency-key': 'idem-1' })
    await apiClient.interceptors.response.handlers?.[0]?.rejected?.(error)

    const payloads = _rateLimitedPayloads(warnSpy)
    expect(payloads).toHaveLength(1)
    expect(payloads[0]).toMatchObject({
      retry_count: 1,
      method: 'post',
      status: 429,
    })
  })
})
