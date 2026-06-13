import { apiClient, unwrap, withSignal } from '../client'
import type { DeliverableReceipt, ReceiptValidationResult } from '@/api/types'
import type { ApiResponse } from '@/api/types/http'

/**
 * Fetch the provenance receipt attached to a deliverable document.
 *
 * The receipt is built in-process when the deliverable completes; this
 * read returns the persisted system-of-record copy.
 */
export async function getDeliverableReceipt(
  projectId: string,
  slug: string,
  signal?: AbortSignal,
): Promise<DeliverableReceipt> {
  const response = await apiClient.get<ApiResponse<DeliverableReceipt>>(
    `/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(slug)}/receipt`,
    withSignal(signal),
  )
  return unwrap(response)
}

/**
 * Validate a deliverable's receipt for signal consistency.
 *
 * Checks that listed sources still resolve, the cassette loads and
 * hashes, and claimed test results match the persisted records. Absent
 * signals are allowed; `valid` is true when all present signals agree.
 */
export async function validateDeliverableReceipt(
  projectId: string,
  slug: string,
  signal?: AbortSignal,
): Promise<ReceiptValidationResult> {
  const response = await apiClient.get<ApiResponse<ReceiptValidationResult>>(
    `/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(slug)}/receipt/validate`,
    withSignal(signal),
  )
  return unwrap(response)
}
