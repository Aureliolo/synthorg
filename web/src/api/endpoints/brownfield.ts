import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  ImportCodebaseAck,
  ImportCodebasePayload as ImportCodebasePayloadWire,
} from '@/api/types/setup'

export type { ImportCodebaseAck }

// The generated wire type marks the server-defaulted fields
// (``title`` / ``requested_by`` / ``default_branch``) as required; the
// client omits them so the backend applies its defaults. Derive the
// request shape from the generated type (no field/type drift) while
// keeping those three optional for the caller.
export type ImportCodebasePayload =
  Pick<ImportCodebasePayloadWire, 'project_id' | 'source_ref'>
  & Partial<Pick<ImportCodebasePayloadWire, 'title' | 'requested_by' | 'default_branch'>>

/**
 * Kick off a brownfield codebase import for a project. Returns the
 * ``202 Accepted`` acknowledgement; the import + analysis run completes
 * asynchronously and is observed via the project's structure map / tasks.
 */
export async function importCodebase(
  payload: ImportCodebasePayload,
): Promise<ImportCodebaseAck> {
  const response = await apiClient.post<ApiResponse<ImportCodebaseAck>>(
    '/brownfield/import',
    payload,
  )
  return unwrap(response)
}
