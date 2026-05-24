import { listArtifacts } from '@/api/endpoints/artifacts'
import { getErrorMessage } from '@/utils/errors'
import type { ArtifactType } from '@/api/types/enums'
import {
  bumpDetailRequestToken,
  isStaleListRequest,
  nextListRequestToken,
} from './_state'
import type { ArtifactsSet } from './types'

async function fetchArtifactsImpl(set: ArtifactsSet): Promise<void> {
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    const result = await listArtifacts({ limit: 200 })
    if (isStaleListRequest(token)) return
    set({ artifacts: result.data, totalArtifacts: result.data.length })
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

function clearDetailImpl(set: ArtifactsSet): void {
  bumpDetailRequestToken()
  set({
    selectedArtifact: null,
    contentPreview: null,
    detailLoading: false,
    detailError: null,
  })
}

export function createListActions(set: ArtifactsSet) {
  return {
    fetchArtifacts: () => fetchArtifactsImpl(set),
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
