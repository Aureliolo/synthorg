import { useCallback, useEffect, useRef } from 'react'

import { useArtifactDetailData } from '@/hooks/useArtifactDetailData'
import { useArtifactsData } from '@/hooks/useArtifactsData'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { ROUTES } from '@/router/routes'

export interface ArtifactDetailPageController {
  artifact: ReturnType<typeof useArtifactDetailData>['artifact']
  contentPreview: ReturnType<typeof useArtifactDetailData>['contentPreview']
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
  showErrorPage: boolean
  showSkeleton: boolean
  showOfflineBanner: boolean
  nav: ReturnType<typeof useDetailNavigation>
  goPrev: () => void
  goNext: () => void
}

export function useArtifactDetailPageController(
  artifactId: string | undefined,
): ArtifactDetailPageController {
  const {
    artifact,
    contentPreview,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useArtifactDetailData(artifactId ?? '')
  // Walk the same filtered list the operator saw on ArtifactsPage so
  // prev/next preserves the user's filter context. ``filteredArtifacts`` is
  // empty on a deep link (the parent list never mounted); the nav bar hides
  // itself in that case via ``position === null``.
  const { filteredArtifacts } = useArtifactsData()
  const routeForArtifact = useCallback(
    (item: { id: string }) =>
      ROUTES.ARTIFACT_DETAIL.replace(':artifactId', encodeURIComponent(item.id)),
    [],
  )
  const nav = useDetailNavigation({
    items: filteredArtifacts,
    currentId: artifactId,
    routeFor: routeForArtifact,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  // Only surface the "real-time updates disconnected" banner once we've
  // successfully connected at least once; otherwise the initial handshake
  // flashes a false-positive offline banner before the socket is ready.
  const hasEverConnectedRef = useRef(false)
  useEffect(() => {
    if (wsConnected) hasEverConnectedRef.current = true
  }, [wsConnected])

  const showErrorPage = Boolean(error) && !artifact
  const showSkeleton = !artifact && !showErrorPage
  const showOfflineBanner =
    !wsConnected &&
    !loading &&
    (hasEverConnectedRef.current || Boolean(wsSetupError))

  return {
    artifact,
    contentPreview,
    loading,
    error,
    wsConnected,
    wsSetupError,
    showErrorPage,
    showSkeleton,
    showOfflineBanner,
    nav,
    goPrev,
    goNext,
  }
}
