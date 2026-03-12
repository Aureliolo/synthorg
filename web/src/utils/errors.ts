/** Error utilities and user-friendly messages. */

import type { AxiosError } from 'axios'
import type { ApiResponse } from '@/api/types'

/**
 * Check if an error is an Axios error.
 */
export function isAxiosError(error: unknown): error is AxiosError {
  return (error as AxiosError)?.isAxiosError === true
}

/**
 * Extract a user-friendly error message from any error.
 */
export function getErrorMessage(error: unknown): string {
  if (isAxiosError(error)) {
    const data = error.response?.data as ApiResponse<unknown> | undefined
    if (data?.error) return data.error

    switch (error.response?.status) {
      case 400:
        return 'Invalid request. Please check your input.'
      case 401:
        return 'Authentication required. Please log in.'
      case 403:
        return 'You do not have permission to perform this action.'
      case 404:
        return 'The requested resource was not found.'
      case 409:
        return 'Conflict: the resource was modified by another user. Please refresh and try again.'
      case 503:
        return 'Service temporarily unavailable. Please try again later.'
      default:
        break
    }

    if (!error.response) {
      return 'Network error. Please check your connection.'
    }

    return `Server error (${error.response.status})`
  }

  if (error instanceof Error) {
    return error.message
  }

  return 'An unexpected error occurred.'
}
