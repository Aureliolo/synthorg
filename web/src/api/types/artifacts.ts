/** Artifact metadata and filter types. */

// The ROW is what the endpoints return: the artifact, with its creator
// already resolved to a name. The dashboard has no other artifact shape.
export type { ArtifactRow as Artifact, CreateArtifactRequest } from './dtos.gen'

/**
 * Frontend-only query filter (not on the wire as a Pydantic DTO).
 *
 * No project field: the endpoint has no project filter, so declaring one
 * here would let a caller pass a narrowing filter the server silently drops.
 */
export interface ArtifactFilters {
  task_id?: string
  created_by?: string
  type?: import('./enums').ArtifactType
  cursor?: string | null
  limit?: number
}
