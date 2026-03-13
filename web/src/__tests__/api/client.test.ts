import { describe, it, expect, beforeEach } from 'vitest'
import { unwrap, unwrapPaginated, apiClient } from '@/api/client'
import { AxiosHeaders, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

function mockResponse<T>(data: T): AxiosResponse {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as AxiosResponse['config'],
  }
}

describe('request interceptor', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  function makeConfig(): InternalAxiosRequestConfig {
    return { headers: new AxiosHeaders() } as InternalAxiosRequestConfig
  }

  function getInterceptor() {
    const handlers = (apiClient.interceptors.request as unknown as { handlers: Array<{ fulfilled?: (c: InternalAxiosRequestConfig) => InternalAxiosRequestConfig }> }).handlers
    const interceptor = handlers?.[0]?.fulfilled
    expect(typeof interceptor).toBe('function')
    return interceptor!
  }

  it('attaches JWT token to request headers', () => {
    localStorage.setItem('auth_token', 'test-jwt-token')
    const config = makeConfig()
    const interceptor = getInterceptor()
    const result = interceptor(config)
    expect(result.headers.get('Authorization')).toBe('Bearer test-jwt-token')
  })

  it('does not attach Authorization when no token', () => {
    const config = makeConfig()
    const interceptor = getInterceptor()
    const result = interceptor(config)
    expect(result.headers.get('Authorization')).toBeUndefined()
  })
})

describe('unwrap', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('extracts data from successful response', () => {
    const response = mockResponse({ data: { id: '1', name: 'test' }, error: null, success: true })
    const result = unwrap(response)
    expect(result).toEqual({ id: '1', name: 'test' })
  })

  it('throws on error response', () => {
    const response = mockResponse({ data: null, error: 'Not found', success: false })
    expect(() => unwrap(response)).toThrow('Not found')
  })

  it('throws on success:false with null data and null error', () => {
    const response = mockResponse({ data: null, error: null, success: false })
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('throws on success:true with null data', () => {
    const response = mockResponse({ data: null, error: null, success: true })
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })
})

describe('unwrapPaginated', () => {
  it('extracts paginated data', () => {
    const response = mockResponse({
      data: [{ id: '1' }, { id: '2' }],
      error: null,
      success: true,
      pagination: { total: 10, offset: 0, limit: 50 },
    })
    const result = unwrapPaginated(response)
    expect(result.data).toHaveLength(2)
    expect(result.total).toBe(10)
    expect(result.offset).toBe(0)
    expect(result.limit).toBe(50)
  })

  it('throws on error', () => {
    const response = mockResponse({
      data: null,
      error: 'Server error',
      success: false,
      pagination: null,
    })
    expect(() => unwrapPaginated(response)).toThrow('Server error')
  })

  it('throws on success with missing pagination', () => {
    const response = mockResponse({
      data: [{ id: '1' }],
      error: null,
      success: true,
      pagination: null,
    })
    expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
  })

  it('throws on success with non-array data', () => {
    const response = mockResponse({
      data: 'not-an-array',
      error: null,
      success: true,
      pagination: { total: 0, offset: 0, limit: 50 },
    })
    expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
  })
})
