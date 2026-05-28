import { useEffect, useRef, useState } from 'react'

import { downloadArtifactContent } from '@/api/endpoints/artifacts'
import { createLogger } from '@/lib/logger'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { Artifact } from '@/api/types/artifacts'

const log = createLogger('ArtifactContentPreview')

export interface ArtifactImagePreview {
  imageSrc: string | null
  imageError: string | null
}

export function useArtifactImagePreview(
  artifact: Artifact,
  isImage: boolean,
): ArtifactImagePreview {
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  const imageSrcRef = useRef<string | null>(null)

  useEffect(() => {
    if (!isImage || artifact.size_bytes === 0) return
    const ctrl = { revoked: false }
    downloadArtifactContent(artifact.id)
      .then((blob) => onPreviewBlobLoaded(blob, ctrl, imageSrcRef, setImageSrc))
      .catch((err: unknown) => onPreviewBlobFailed(err, ctrl, artifact, setImageError))
    return () => {
      ctrl.revoked = true
      setImageSrc(null)
      setImageError(null)
      if (imageSrcRef.current) {
        URL.revokeObjectURL(imageSrcRef.current)
        imageSrcRef.current = null
      }
    }
  }, [artifact.id, isImage, artifact.size_bytes, artifact.content_type, artifact])

  return { imageSrc, imageError }
}

function onPreviewBlobLoaded(
  blob: Blob,
  ctrl: { revoked: boolean },
  imageSrcRef: { current: string | null },
  setImageSrc: (src: string | null) => void,
): void {
  if (ctrl.revoked) return
  const url = URL.createObjectURL(blob)
  imageSrcRef.current = url
  setImageSrc(url)
}

function onPreviewBlobFailed(
  err: unknown,
  ctrl: { revoked: boolean },
  artifact: Artifact,
  setImageError: (msg: string | null) => void,
): void {
  if (ctrl.revoked) return
  const message = getErrorMessage(err)
  // Structured log so an operator chasing missing previews can tell whether
  // this is a 404 (artifact gone), a 5xx (storage backend issue), or a
  // network failure. Don't include the artifact path; it can carry
  // user-content that might be sensitive in logs.
  // SEC-1: every dynamic string passed into the structured log payload goes
  // through sanitizeForLog. artifact.id / artifact.content_type can carry
  // user-controlled bytes; statusCode is bounded to a number or null.
  log.error('artifact image preview failed to load', {
    artifactId: sanitizeForLog(artifact.id),
    contentType: sanitizeForLog(artifact.content_type),
    statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
    error: sanitizeForLog(message),
  })
  setImageError(message)
}
