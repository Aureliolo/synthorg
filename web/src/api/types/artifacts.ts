/** Artifact metadata and filter types. */

export type { Artifact, CreateArtifactRequest } from './dtos.gen'

/** Frontend-only query filter (not on the wire as a Pydantic DTO). */
export interface ArtifactFilters {
  task_id?: string
  created_by?: string
  type?: import('./enums').ArtifactType
  project_id?: string
  offset?: number
  limit?: number
}
