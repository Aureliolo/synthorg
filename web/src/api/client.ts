/**
 * Axios client with JWT interceptor and ApiResponse envelope unwrapping.
 */

import axios, { type AxiosError, type AxiosResponse } from 'axios'
import type { ApiResponse, PaginatedResponse } from './types'

// Normalize: strip trailing slashes and any existing /api/v1 suffix
const RAW_BASE = (import.meta.env.VITE_API_BASE_URL as string) || ''
const BASE_URL = RAW_BASE.replace(/\/+$/, '').replace(/\/api\/v1\/?$/, '')

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

// ── Response interceptor: 401 redirect + error passthrough ──

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError<{ error?: string; success?: boolean }>) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_token_expires_at')
      // Use router import for SPA-friendly navigation (preserves in-memory state)
      if (window.location.pathname !== '/login' && window.location.pathname !== '/setup') {
        // Dynamic import to avoid circular dependency with router -> stores -> api
        import('@/router').then(({ router }) => {
          router.push('/login')
        }).catch(() => {
          window.location.href = '/login'
        })
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
 * Validates the response structure to avoid cryptic TypeErrors.
 */
export function unwrapPaginated<T>(
  response: AxiosResponse<PaginatedResponse<T>>,
): { data: T[]; total: number; offset: number; limit: number } {
  const body = response.data
  if (!body.success) {
    throw new Error(body.error ?? 'Unknown API error')
  }
  if (!body.pagination || !Array.isArray(body.data)) {
    throw new Error('Unexpected API response format')
  }
  return {
    data: body.data,
    total: body.pagination.total,
    offset: body.pagination.offset,
    limit: body.pagination.limit,
  }
}
