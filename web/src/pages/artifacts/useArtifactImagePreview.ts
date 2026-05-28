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
    const artifactId = artifact.id
    const contentType = artifact.content_type
    downloadArtifactContent(artifactId)
      .then((blob) => onPreviewBlobLoaded(blob, ctrl, imageSrcRef, setImageSrc))
      .catch((err: unknown) => onPreviewBlobFailed(err, ctrl, artifactId, contentType, setImageError))
    return () => {
      ctrl.revoked = true
      setImageSrc(null)
      setImageError(null)
      if (imageSrcRef.current) {
        URL.revokeObjectURL(imageSrcRef.current)
        imageSrcRef.current = null
      }
    }
  }, [artifact.id, isImage, artifact.size_bytes, artifact.content_type])

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
  artifactId: string,
  contentType: string | null | undefined,
  setImageError: (msg: string | null) => void,
): void {
  if (ctrl.revoked) return
  const message = getErrorMessage(err)
  // SEC-1: artifactId / contentType can carry user-controlled bytes; route
  // through sanitizeForLog. statusCode is bounded to a number or null.
  log.error('artifact image preview failed to load', {
    artifactId: sanitizeForLog(artifactId),
    contentType: sanitizeForLog(contentType),
    statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
    error: sanitizeForLog(message),
  })
  setImageError(message)
}
