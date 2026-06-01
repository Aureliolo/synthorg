import { apiClient, unwrap } from '../client'
import type { LearningCurve } from '../types'
import type { ApiResponse } from '../types/http'

/**
 * Fetch the benchmark learning curve assembled from recorded
 * golden-company scorecard runs. Returns an empty curve when no
 * benchmark history is configured (not an error).
 */
export async function getLearningCurve(): Promise<LearningCurve> {
  const response = await apiClient.get<ApiResponse<LearningCurve>>('/learning/curve')
  return unwrap(response)
}
