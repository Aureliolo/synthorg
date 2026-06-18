import { apiClient, unwrap } from '../client'
import type { DecompositionResult, ManualDecomposeRequest } from '../types'
import type { ApiResponse } from '../types/http'

/**
 * Run a hand-authored decomposition plan against a task.
 *
 * Posts the manual subtask breakdown to
 * ``POST /tasks/{taskId}/decompose`` and returns the validated
 * :type:`DecompositionResult` (the plan plus the created child tasks
 * and dependency edges) for the dashboard to render.
 */
export async function decomposeTaskManually(
  taskId: string,
  data: ManualDecomposeRequest,
): Promise<DecompositionResult> {
  const response = await apiClient.post<ApiResponse<DecompositionResult>>(
    `/tasks/${encodeURIComponent(taskId)}/decompose`,
    data,
  )
  return unwrap(response)
}
