import type { StoreApi } from 'zustand'
import type {
  Artifact,
  ArtifactType,
  CreateArtifactRequest,
  WsEvent,
} from '@/api/types'

export interface ArtifactsState {
  // List page
  artifacts: readonly Artifact[]
  /** Opaque cursor for the next page; null on the final page. */
  nextCursor: string | null
  /** Whether more items follow the current page. */
  hasMore: boolean
  listLoading: boolean
  listError: string | null

  // Filters
  searchQuery: string
  typeFilter: ArtifactType | null
  createdByFilter: string | null
  taskIdFilter: string | null
  contentTypeFilter: string | null
  projectIdFilter: string | null

  // Detail page
  selectedArtifact: Artifact | null
  contentPreview: string | null
  detailLoading: boolean
  detailError: string | null

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`false`) on failure.
  fetchArtifacts: () => Promise<void>
  fetchMoreArtifacts: () => Promise<void>
  fetchArtifactDetail: (id: string) => Promise<void>
  createArtifact: (data: CreateArtifactRequest) => Promise<Artifact | null>
  deleteArtifact: (id: string) => Promise<boolean>
  setSearchQuery: (q: string) => void
  setTypeFilter: (t: ArtifactType | null) => void
  setCreatedByFilter: (c: string | null) => void
  setTaskIdFilter: (t: string | null) => void
  setContentTypeFilter: (ct: string | null) => void
  setProjectIdFilter: (p: string | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

export type ArtifactsSet = StoreApi<ArtifactsState>['setState']
export type ArtifactsGet = StoreApi<ArtifactsState>['getState']
