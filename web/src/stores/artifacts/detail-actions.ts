import { getArtifact, getArtifactContentText } from '@/api/endpoints/artifacts'
import { getErrorMessage } from '@/utils/errors'
import type { Artifact } from '@/api/types'
import {
  getPendingDetailId,
  isStaleDetailRequest,
  nextDetailRequestToken,
  setPendingDetailId,
} from './_state'
import type { ArtifactsSet } from './types'

/** Content types eligible for inline text preview: text/*, application/json, and YAML.
 *
 * Normalises the wire value first by stripping any ``; charset=...`` suffix
 * and lowercasing the bare media type so headers like
 * ``application/json; charset=utf-8`` still match.
 */
function isPreviewableText(contentType: string): boolean {
  const baseType = (contentType.split(';')[0] ?? contentType).trim().toLowerCase()
  return (
    baseType.startsWith('text/')
    || baseType === 'application/json'
    || baseType === 'application/yaml'
    || baseType === 'application/x-yaml'
  )
}

function shouldFetchPreview(artifact: Artifact): boolean {
  return Boolean(
    artifact.content_type
    && artifact.size_bytes > 0
    && isPreviewableText(artifact.content_type),
  )
}

async function fetchPreviewImpl(
  set: ArtifactsSet,
  id: string,
  token: number,
): Promise<void> {
  try {
    const preview = await getArtifactContentText(id)
    if (isStaleDetailRequest(token)) return
    set({ contentPreview: preview })
  } catch (err) {
    if (isStaleDetailRequest(token)) return
    // Two-clause copy: title-style preface tells the user the
    // metadata IS fine, then offers the next action. The raw
    // backend message is routed through getErrorMessage so a
    // JSON / stack trace cannot leak through.
    set({
      detailError:
        `Preview failed to load: ${getErrorMessage(err)}. The full metadata is shown above; try again or download to view offline.`,
    })
  }
}

async function fetchArtifactDetailImpl(
  set: ArtifactsSet,
  id: string,
): Promise<void> {
  const token = nextDetailRequestToken()
  setPendingDetailId(id)
  set({
    detailLoading: true,
    detailError: null,
    selectedArtifact: null,
    contentPreview: null,
  })
  try {
    const artifact = await getArtifact(id)
    if (isStaleDetailRequest(token)) return
    // Show metadata immediately so the detail page renders while preview loads.
    set({ selectedArtifact: artifact })
    if (shouldFetchPreview(artifact)) {
      await fetchPreviewImpl(set, id, token)
    }
  } catch (err) {
    if (isStaleDetailRequest(token)) return
    set({
      detailError: getErrorMessage(err),
      selectedArtifact: null,
      contentPreview: null,
    })
  } finally {
    // Only clear pendingDetailId when this response is not stale; a
    // stale earlier request must not wipe the pending id set by a
    // newer same-id request, or a later delete could miss invalidation
    // and repopulate deleted detail data.
    if (!isStaleDetailRequest(token) && getPendingDetailId() === id) {
      setPendingDetailId(null)
    }
    if (!isStaleDetailRequest(token)) set({ detailLoading: false })
  }
}

export function createDetailActions(set: ArtifactsSet) {
  return {
    fetchArtifactDetail: (id: string) => fetchArtifactDetailImpl(set, id),
  }
}
