/** Project domain types. */

import type { ProjectStatus } from './enums'

export type {
  ContributorRef,
  CreateProjectRequest,
  ProjectAutonomyModeRequest,
  ProjectProgress,
  ProjectProgressItem,
  // The ROW is the only project shape any HTTP response returns: the
  // project plus its lead's resolved name. The dashboard has no other, so
  // it is imported under the domain name.
  ProjectRow as Project,
} from './dtos.gen'

/** Frontend-only query filter (not a Pydantic DTO). */
export interface ProjectFilters {
  status?: ProjectStatus
  lead?: string
  cursor?: string | null
  limit?: number
}
