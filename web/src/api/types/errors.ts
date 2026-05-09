/** RFC 9457 structured error types mirroring backend `synthorg.api.errors`. */

import { ErrorCategory, ErrorCode } from './error-codes.gen'

// Re-export the generated constants and value-typed unions so the
// rest of the dashboard imports error metadata from a single module.
// The source-of-truth lives in ``src/synthorg/core/error_taxonomy.py``;
// regenerate ``error-codes.gen.ts`` with
// ``uv run python scripts/generate_error_codes_ts.py`` after adding a
// new code on the backend. Drift is enforced at pre-push by
// ``scripts/check_error_codes_ts_in_sync.py``.
export { ErrorCategory, ErrorCode }

export interface ErrorDetail {
  detail: string
  error_code: ErrorCode
  error_category: ErrorCategory
  retryable: boolean
  retry_after: number | null
  instance: string
  title: string
  type: string
}
