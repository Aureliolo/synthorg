import { listArtifacts } from '@/api/endpoints/artifacts'
import { getErrorMessage } from '@/utils/errors'
import type { ArtifactType } from '@/api/types'
import {
  bumpDetailRequestToken,
  isStaleListRequest,
  nextListRequestToken,
} from './_state'
import type { ArtifactsGet, ArtifactsSet } from './types'

const ARTIFACTS_PAGE_LIMIT = 200

async function fetchArtifactsImpl(set: ArtifactsSet): Promise<void> {
  const token = nextListRequestToken()
  set({
    listLoading: true,
    listError: null,
    nextCursor: null,
    hasMore: false,
  })
  try {
    const result = await listArtifacts({ limit: ARTIFACTS_PAGE_LIMIT })
    if (isStaleListRequest(token)) return
    set({
      artifacts: result.data,
      nextCursor: result.nextCursor,
      hasMore: result.hasMore,
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
    // Always clear ``listLoading`` for the latest request -- including
    // the stale-return paths -- so an overlapping fetch can't leave
    // the skeleton stuck on.
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

async function fetchMoreArtifactsImpl(
  set: ArtifactsSet,
  get: ArtifactsGet,
): Promise<void> {
  const { listLoading, hasMore, nextCursor } = get()
  if (listLoading || !hasMore || !nextCursor) return
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    const result = await listArtifacts({
      cursor: nextCursor,
      limit: ARTIFACTS_PAGE_LIMIT,
    })
    if (isStaleListRequest(token)) return
    set((s) => {
      const existingIds = new Set(s.artifacts.map((a) => a.id))
      const deduped = result.data.filter((a) => !existingIds.has(a.id))
      return {
        artifacts: [...s.artifacts, ...deduped],
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      }
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

function clearDetailImpl(set: ArtifactsSet): void {
  bumpDetailRequestToken()
  set({
    selectedArtifact: null,
    contentPreview: null,
    detailLoading: false,
    detailError: null,
  })
}

export function createListActions(set: ArtifactsSet, get: ArtifactsGet) {
  return {
    fetchArtifacts: () => fetchArtifactsImpl(set),
    fetchMoreArtifacts: () => fetchMoreArtifactsImpl(set, get),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setTypeFilter: (t: ArtifactType | null) => set({ typeFilter: t }),
    setCreatedByFilter: (c: string | null) => set({ createdByFilter: c }),
    setTaskIdFilter: (t: string | null) => set({ taskIdFilter: t }),
    setContentTypeFilter: (ct: string | null) =>
      set({ contentTypeFilter: ct }),
    setProjectIdFilter: (p: string | null) => set({ projectIdFilter: p }),
    clearDetail: () => clearDetailImpl(set),
  }
}
