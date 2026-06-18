import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

export interface ImportCodebasePayload {
  project_id: string
  source_ref: string
  title?: string
  requested_by?: string
  default_branch?: string
}

export interface ImportCodebaseAck {
  project_id: string
  status: string
}

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
