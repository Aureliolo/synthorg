/** Project domain types. */

import type { ProjectStatus } from './enums'

export interface Project {
  id: string
  name: string
  description: string
  team: readonly string[]
  lead: string | null
  task_ids: readonly string[]
  deadline: string | null
  budget: number
  status: ProjectStatus
}

export interface CreateProjectRequest {
  name: string
  description?: string
  team?: string[]
  lead?: string | null
  deadline?: string
  budget?: number
}

export interface ProjectFilters {
  status?: ProjectStatus
  lead?: string
  cursor?: string | null
  limit?: number
}
