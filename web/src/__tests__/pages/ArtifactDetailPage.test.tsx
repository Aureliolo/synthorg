import fc from 'fast-check'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { UseArtifactDetailDataReturn } from '@/hooks/useArtifactDetailData'
import { makeArtifact } from '../helpers/factories'


vi.mock('@/pages/artifacts/ArtifactDetailSkeleton', () => ({
  ArtifactDetailSkeleton: () => <div data-testid="artifact-detail-skeleton" />,
}))
vi.mock('@/pages/artifacts/ArtifactMetadata', () => ({
  ArtifactMetadata: () => <div data-testid="artifact-metadata" />,
}))
vi.mock('@/pages/artifacts/ArtifactContentPreview', () => ({
  ArtifactContentPreview: () => <div data-testid="artifact-content-preview" />,
}))


const artifact = makeArtifact('artifact-001')

const defaultHookReturn: UseArtifactDetailDataReturn = {
  artifact,
  contentPreview: null,
  loading: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultHookReturn }

const getDetailData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useArtifactDetailData', () => {
  const hookName = 'useArtifactDetailData'
  return { [hookName]: () => getDetailData() }
})

import ArtifactDetailPage from '@/pages/ArtifactDetailPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/artifacts/artifact-001']}>
      <Routes>
        <Route path="/artifacts/:artifactId" element={<ArtifactDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ArtifactDetailPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
  })

  it('renders loading skeleton when loading with no artifact', () => {
    hookReturn = { ...defaultHookReturn, artifact: null, loading: true }
    renderPage()
    expect(screen.getByTestId('artifact-detail-skeleton')).toBeInTheDocument()
  })

  it('renders not found when no artifact and error is set', () => {
    hookReturn = {
      ...defaultHookReturn,
      artifact: null,
      error: 'Artifact missing',
    }
    renderPage()
    expect(screen.getByText('Artifact not found')).toBeInTheDocument()
  })

  it('renders metadata and content preview when artifact loaded', () => {
    renderPage()
    expect(screen.getByTestId('artifact-metadata')).toBeInTheDocument()
    expect(screen.getByTestId('artifact-content-preview')).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Failed to load' }
    renderPage()
    expect(screen.getByText('Failed to load')).toBeInTheDocument()
  })

  it('renders breadcrumb link to artifacts', () => {
    renderPage()
    expect(screen.getByRole('link', { name: 'Artifacts' })).toBeInTheDocument()
  })

  describe('property-based state transitions', () => {
    it('shows skeleton whenever there is no artifact and no error', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (loading, hasArtifact) => {
          hookReturn = {
            ...defaultHookReturn,
            artifact: hasArtifact ? artifact : null,
            loading,
          }
          const { unmount } = renderPage()
          const hasSkeleton = screen.queryByTestId('artifact-detail-skeleton') !== null
          unmount()
          return hasSkeleton === !hasArtifact
        }),
        { numRuns: 20 },
      )
    })

    it('shows not-found only when no artifact and error is set', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (hasError, hasArtifact) => {
          hookReturn = {
            ...defaultHookReturn,
            artifact: hasArtifact ? artifact : null,
            error: hasError ? 'Artifact missing' : null,
          }
          const { unmount } = renderPage()
          const hasNotFound = screen.queryByText('Artifact not found') !== null
          unmount()
          return hasNotFound === (hasError && !hasArtifact)
        }),
        { numRuns: 10 },
      )
    })

    it('shows error banner when error is set', () => {
      fc.assert(
        fc.property(
          fc.lorem({ maxCount: 3 }),
          (errorMsg) => {
            hookReturn = { ...defaultHookReturn, error: errorMsg }
            const { unmount } = renderPage()
            const hasError = screen.queryByText(errorMsg) !== null
            unmount()
            return hasError
          },
        ),
        { numRuns: 20 },
      )
    })
  })
})
