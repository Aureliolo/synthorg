import { ErrorCode } from '@/api/types/errors'
import { getErrorDetail, getErrorMessage, unavailableMessage } from '@/utils/errors'

const FEATURE_UNAVAILABLE_TITLE = 'Conversational mode unavailable'

/**
 * Build the toast title + description for a conversational action failure.
 *
 * A SERVICE_UNAVAILABLE (503) from the conversational endpoints is the
 * deliberate fail-closed state (a capability is disabled, or direct-MCP acting
 * lacks security governance), not a transient outage, so it gets a distinct
 * title and surfaces the backend's specific reason rather than the generic
 * "try again" copy.
 */
export function describeConversationalError(
  err: unknown,
  fallbackTitle: string,
): { title: string; description: string } {
  if (getErrorDetail(err)?.error_code === ErrorCode.SERVICE_UNAVAILABLE) {
    return {
      title: FEATURE_UNAVAILABLE_TITLE,
      description: unavailableMessage(
        err,
        'This conversational mode is not enabled. Ask your administrator to enable it.',
      ),
    }
  }
  return { title: fallbackTitle, description: getErrorMessage(err) }
}
