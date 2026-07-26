/** Artifact metadata and filter types. */

export type { Artifact, CreateArtifactRequest } from './dtos.gen'

/**
 * Frontend-only query filter (not on the wire as a Pydantic DTO).
 *
 * No project field: the endpoint has no project filter, so declaring one
 * here would let a caller pass a narrowing the server silently drops.
 */
export interface ArtifactFilters {
  task_id?: string
  created_by?: string
  type?: import('./enums').ArtifactType
  cursor?: string | null
  limit?: number
}
