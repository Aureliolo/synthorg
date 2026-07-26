/** Artifact metadata and filter types. */

export type { Artifact, CreateArtifactRequest } from './dtos.gen'

/**
 * Frontend-only query filter (not on the wire as a Pydantic DTO).
 *
 * Every field here is a query parameter `GET /artifacts` actually accepts.
 * Project narrowing is deliberately absent: the endpoint has no project
 * filter, so a `project_id` here would be dropped server-side with no
 * error. `useArtifactsData` narrows by `Artifact.project_id` on the client
 * instead, using the field the response DTO does carry.
 */
export interface ArtifactFilters {
  task_id?: string
  created_by?: string
  type?: import('./enums').ArtifactType
  cursor?: string | null
  limit?: number
}
