/** Project domain types. */

import type { ProjectStatus } from './enums'

export type {
  CreateProjectRequest,
  Project,
  ProjectAutonomyModeRequest,
} from './dtos.gen'

/** Frontend-only query filter (not a Pydantic DTO). */
export interface ProjectFilters {
  status?: ProjectStatus
  lead?: string
  cursor?: string | null
  limit?: number
}
