import type { StoreApi } from 'zustand'
import type { Artifact, CreateArtifactRequest } from '@/api/types/artifacts'
import type { ArtifactType } from '@/api/types/enums'
import type { WsEvent } from '@/api/types/websocket'

export interface ArtifactsState {
  // List page
  artifacts: readonly Artifact[]
  totalArtifacts: number
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
