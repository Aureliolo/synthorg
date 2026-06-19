import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

export interface SubmitObjectivePayload {
  title: string
  description: string
  requested_by: string
  priority?: string | null
  estimated_complexity?: string | null
  task_type?: string | null
  acceptance_criteria?: readonly string[]
}

export interface SubmitObjectiveAck {
  submission_id: string
  status: string
}

/**
 * Submit a goal/objective for decomposition. Returns the ``202 Accepted``
 * acknowledgement with a server-minted ``submission_id`` that correlates
 * to the spawned root task once the pipeline run materialises it.
 */
export async function submitObjective(
  payload: SubmitObjectivePayload,
): Promise<SubmitObjectiveAck> {
  const response = await apiClient.post<ApiResponse<SubmitObjectiveAck>>(
    '/objectives',
    payload,
  )
  return unwrap(response)
}
