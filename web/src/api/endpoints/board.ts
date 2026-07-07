import { apiClient, unwrap } from '../client'
import type { KanbanBoardView } from '../types/board'
import type { ApiResponse } from '../types/http'

/** Fetch the org's Kanban board: per-column cards, counts, and WIP state. */
export async function getBoard(project?: string): Promise<KanbanBoardView> {
  const params = project ? { project } : undefined
  const response = await apiClient.get<ApiResponse<KanbanBoardView>>('/board', { params })
  return unwrap(response)
}
