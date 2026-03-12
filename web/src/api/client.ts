/**
 * Axios client with JWT interceptor and ApiResponse envelope unwrapping.
 */

import axios, { type AxiosError, type AxiosResponse } from 'axios'
import type { ApiResponse } from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export const apiClient = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
})

// ── Request interceptor: attach JWT ──────────────────────────

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── Response interceptor: unwrap envelope + error handling ───

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError<ApiResponse<unknown>>) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      if (window.location.pathname !== '/login' && window.location.pathname !== '/setup') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

/**
 * Extract data from an ApiResponse envelope.
 * Throws if the response indicates an error.
 */
export function unwrap<T>(response: AxiosResponse<ApiResponse<T>>): T {
  const body = response.data
  if (!body.success || body.data === null || body.data === undefined) {
    throw new Error(body.error ?? 'Unknown API error')
  }
  return body.data
}

/**
 * Extract data from a paginated response.
 */
export function unwrapPaginated<T>(
  response: AxiosResponse,
): { data: T[]; total: number; offset: number; limit: number } {
  const body = response.data
  if (!body.success) {
    throw new Error(body.error ?? 'Unknown API error')
  }
  return {
    data: body.data,
    total: body.pagination.total,
    offset: body.pagination.offset,
    limit: body.pagination.limit,
  }
}
