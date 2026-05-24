import type { AxiosError } from 'axios'

import { isAxiosError, getErrorMessage } from '@/utils/errors'

export interface ClassifiedError {
  message: string
  /** HTTP status if available (server-side error). */
  status?: number
  /** Transient failures that retry may resolve (5xx, timeout, network). */
  isTransient: boolean
  /** Client-caused errors (4xx) that typically require user action. */
  isClient: boolean
  /** Whether automatic retry is advisable. */
  retryable: boolean
  /** When classified as `client`, an optional action hint (e.g. "Check your permissions"). */
  guidance?: string
}

type StatusClassifier = (message: string, status: number) => ClassifiedError

/**
 * Per-status classification rules. Statuses not present here fall
 * through to the generic 4xx / 5xx ladder in `_classifyAxiosWithResponse`.
 */
const HTTP_STATUS_CLASSIFIERS: Readonly<Record<number, StatusClassifier>> = {
  401: (message, status) => ({
    message,
    status,
    isTransient: false,
    isClient: true,
    retryable: false,
    guidance: 'Your session may have expired. Please sign in again.',
  }),
  403: (message, status) => ({
    message,
    status,
    isTransient: false,
    isClient: true,
    retryable: false,
    guidance: 'You do not have permission for this action. Contact an administrator.',
  }),
  404: (message, status) => ({
    message,
    status,
    isTransient: false,
    isClient: true,
    retryable: false,
    guidance: 'The requested resource was not found. It may have been deleted.',
  }),
  408: (message, status) => ({
    message,
    status,
    isTransient: true,
    isClient: false,
    retryable: true,
  }),
  409: (message, status) => ({
    message,
    status,
    isTransient: false,
    isClient: true,
    // Conflicts require user action (refresh + re-submit with new state);
    // callers that loop on `retryable` would otherwise thrash against the
    // server without resolution.
    retryable: false,
    guidance: 'Someone else modified this resource. Refresh and try again.',
  }),
  429: (message, status) => ({
    message,
    status,
    isTransient: true,
    isClient: false,
    retryable: true,
    guidance: 'Rate limited. Wait a moment before retrying.',
  }),
}

function _defaultClassification(message: string): ClassifiedError {
  // Non-axios errors (TypeError, SyntaxError, ...) default to non-retryable.
  return { message, isTransient: false, isClient: false, retryable: false }
}

function _classifyAxiosWithResponse(
  message: string,
  status: number,
): ClassifiedError {
  if (status >= 500) {
    return { message, status, isTransient: true, isClient: false, retryable: true }
  }
  const specific = HTTP_STATUS_CLASSIFIERS[status]
  if (specific) return specific(message, status)
  if (status >= 400 && status < 500) {
    return {
      message,
      status,
      isTransient: false,
      isClient: true,
      retryable: false,
      guidance: 'Check your input and try again.',
    }
  }
  return _defaultClassification(message)
}

function _classifyAxiosError(error: AxiosError, message: string): ClassifiedError {
  // Canceled requests (AbortController / axios cancel) are not retryable.
  // Emit them with the normal shape but no transient/retryable flag so
  // callers don't loop a user-initiated cancel.
  if (error.code === 'ERR_CANCELED') {
    return {
      message,
      isTransient: false,
      isClient: false,
      retryable: false,
      guidance: 'Request was canceled.',
    }
  }
  if (!error.response) {
    return {
      message,
      isTransient: true,
      isClient: false,
      retryable: true,
      guidance: 'Check your network connection and try again.',
    }
  }
  return _classifyAxiosWithResponse(message, error.response.status)
}

/**
 * Classify an error into transient / client / unknown categories so the UI
 * can choose between "Retry" (transient) vs "Check configuration" (client)
 * affordances. Used by onboarding surfaces and list-fetch error banners.
 */
export function classifyError(error: unknown): ClassifiedError {
  const message = getErrorMessage(error)
  if (isAxiosError(error)) return _classifyAxiosError(error, message)
  return _defaultClassification(message)
}
