import {
  createArtifact as createArtifactApi,
  deleteArtifact as deleteArtifactApi,
} from '@/api/endpoints/artifacts'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  Artifact,
  CreateArtifactRequest,
} from '@/api/types/artifacts'
import {
  bumpDetailRequestToken,
  bumpListRequestToken,
  getPendingDetailId,
  setPendingDetailId,
} from './_state'
import type { ArtifactsGet, ArtifactsSet, ArtifactsState } from './types'

const log = createLogger('artifacts')

async function createArtifactImpl(
  set: ArtifactsSet,
  data: CreateArtifactRequest,
): Promise<Artifact | null> {
  try {
    const created = await createArtifactApi(data)
    // Bump the list-token so any in-flight ``listArtifacts`` resolves
    // as stale and cannot overwrite this optimistic insert with an
    // older snapshot.
    bumpListRequestToken()
    set((state) => {
      const exists = state.artifacts.some((a) => a.id === created.id)
      const filtered = state.artifacts.filter((a) => a.id !== created.id)
      return {
        artifacts: [created, ...filtered],
        totalArtifacts: exists
          ? state.totalArtifacts
          : state.totalArtifacts + 1,
        // Bumping the list token strands any in-flight ``fetchArtifacts``
        // -- it bails on the stale check without ever clearing
        // ``listLoading``. Reset it here so the page does not stay on
        // the skeleton when a create lands during the initial load.
        listLoading: false,
      }
    })
    useToastStore.getState().add({
      variant: 'success',
      title: 'Artifact created',
    })
    return created
  } catch (err) {
    log.error(
      'Create artifact failed:',
      sanitizeForLog({ path: data.path, error: err }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create artifact',
      description: getErrorMessage(err),
    })
    return null
  }
}

interface DeleteSnapshot {
  artifacts: ArtifactsState['artifacts']
  totalArtifacts: number
  selectedArtifact: ArtifactsState['selectedArtifact']
  contentPreview: ArtifactsState['contentPreview']
  detailLoading: boolean
  detailError: ArtifactsState['detailError']
}

function captureSnapshot(state: ArtifactsState): DeleteSnapshot {
  return {
    artifacts: state.artifacts,
    totalArtifacts: state.totalArtifacts,
    selectedArtifact: state.selectedArtifact,
    contentPreview: state.contentPreview,
    detailLoading: state.detailLoading,
    detailError: state.detailError,
  }
}

interface DeleteContext {
  snapshot: DeleteSnapshot
  invalidatesDetail: boolean
}

function invalidateDetailIfTargeted(
  id: string,
  selectedId: string | undefined,
): boolean {
  const invalidatesPendingDetail = getPendingDetailId() === id
  if (invalidatesPendingDetail) {
    bumpDetailRequestToken()
    setPendingDetailId(null)
  }
  const isSelected = selectedId === id
  if (isSelected && !invalidatesPendingDetail) bumpDetailRequestToken()
  return isSelected || invalidatesPendingDetail
}

function applyOptimisticDelete(
  set: ArtifactsSet,
  id: string,
  state: ArtifactsState,
): DeleteContext {
  const snapshot = captureSnapshot(state)
  bumpListRequestToken()
  const invalidatesDetail = invalidateDetailIfTargeted(
    id,
    state.selectedArtifact?.id,
  )
  set({
    artifacts: state.artifacts.filter((a) => a.id !== id),
    totalArtifacts: Math.max(0, state.totalArtifacts - 1),
    selectedArtifact: invalidatesDetail ? null : state.selectedArtifact,
    contentPreview: invalidatesDetail ? null : state.contentPreview,
    detailLoading: invalidatesDetail ? false : state.detailLoading,
    detailError: invalidatesDetail ? null : state.detailError,
  })
  return { snapshot, invalidatesDetail }
}

function restoreOnDeleteFailure(
  set: ArtifactsSet,
  context: DeleteContext,
): void {
  // Restore the pre-delete slice so the list/detail view reflects
  // the server's truth after the failed mutation. ``detailLoading``
  // is forced to ``false`` when the delete had invalidated a
  // pending detail fetch via the token bump -- we can't un-bump
  // the tokens now, so the in-flight fetch will be ignored, and
  // leaving ``detailLoading=true`` would otherwise strand the
  // detail pane on its spinner until the user navigates away.
  set(
    context.invalidatesDetail
      ? { ...context.snapshot, detailLoading: false }
      : context.snapshot,
  )
}

async function deleteArtifactImpl(
  set: ArtifactsSet,
  get: ArtifactsGet,
  id: string,
): Promise<boolean> {
  const context = applyOptimisticDelete(set, id, get())
  try {
    await deleteArtifactApi(id)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Artifact deleted',
    })
    return true
  } catch (err) {
    restoreOnDeleteFailure(set, context)
    log.error(
      'Delete artifact failed:',
      sanitizeForLog({ artifactId: id, error: err }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to delete artifact',
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createCrudActions(set: ArtifactsSet, get: ArtifactsGet) {
  return {
    createArtifact: (data: CreateArtifactRequest) =>
      createArtifactImpl(set, data),
    deleteArtifact: (id: string) => deleteArtifactImpl(set, get, id),
  }
}
