import { describe, it, expect, beforeEach } from 'vitest'
import { unwrap, unwrapPaginated } from '@/api/client'
import type { AxiosResponse } from 'axios'

function mockResponse<T>(data: T): AxiosResponse {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as AxiosResponse['config'],
  }
}

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

  it('throws on null data', () => {
    const response = mockResponse({ data: null, error: null, success: false })
    expect(() => unwrap(response)).toThrow()
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
      data: [],
      error: 'Server error',
      success: false,
      pagination: { total: 0, offset: 0, limit: 50 },
    })
    expect(() => unwrapPaginated(response)).toThrow('Server error')
  })
})
