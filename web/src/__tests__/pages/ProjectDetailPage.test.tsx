import fc from 'fast-check'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { UseProjectDetailDataReturn } from '@/hooks/useProjectDetailData'
import { makeProject } from '../helpers/factories'


vi.mock('@/pages/projects/ProjectDetailSkeleton', () => ({
  ProjectDetailSkeleton: () => <div data-testid="project-detail-skeleton" />,
}))
vi.mock('@/pages/projects/ProjectHeader', () => ({
  ProjectHeader: () => <div data-testid="project-header" />,
}))
vi.mock('@/pages/projects/ProjectTeamSection', () => ({
  ProjectTeamSection: () => <div data-testid="project-team-section" />,
}))
vi.mock('@/pages/projects/ProjectTaskList', () => ({
  ProjectTaskList: () => <div data-testid="project-task-list" />,
}))


const project = makeProject('proj-001')

const defaultHookReturn: UseProjectDetailDataReturn = {
  project,
  projectTasks: [],
  loading: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultHookReturn }

const getDetailData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useProjectDetailData', () => {
  const hookName = 'useProjectDetailData'
  return { [hookName]: () => getDetailData() }
})

import ProjectDetailPage from '@/pages/ProjectDetailPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projects/proj-001']}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
  })

  it('renders loading skeleton when loading with no project', () => {
    hookReturn = { ...defaultHookReturn, project: null, loading: true }
    renderPage()
    expect(screen.getByTestId('project-detail-skeleton')).toBeInTheDocument()
  })

  it('renders not found when no project and error is set', () => {
    hookReturn = {
      ...defaultHookReturn,
      project: null,
      error: 'Project missing',
    }
    renderPage()
    expect(screen.getByText('Project not found')).toBeInTheDocument()
  })

  it('renders header, team section, and task list when project loaded', () => {
    renderPage()
    expect(screen.getByTestId('project-header')).toBeInTheDocument()
    expect(screen.getByTestId('project-team-section')).toBeInTheDocument()
    expect(screen.getByTestId('project-task-list')).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Failed to load' }
    renderPage()
    expect(screen.getByText('Failed to load')).toBeInTheDocument()
  })

  it('renders breadcrumb link to projects', () => {
    renderPage()
    expect(screen.getByRole('link', { name: 'Projects' })).toBeInTheDocument()
  })

  describe('property-based state transitions', () => {
    it('shows skeleton whenever there is no project and no error', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (loading, hasProject) => {
          hookReturn = {
            ...defaultHookReturn,
            project: hasProject ? project : null,
            loading,
          }
          const { unmount } = renderPage()
          const hasSkeleton = screen.queryByTestId('project-detail-skeleton') !== null
          unmount()
          return hasSkeleton === !hasProject
        }),
        { numRuns: 20 },
      )
    })

    it('shows not-found only when no project and error is set', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (hasError, hasProject) => {
          hookReturn = {
            ...defaultHookReturn,
            project: hasProject ? project : null,
            error: hasError ? 'Project missing' : null,
          }
          const { unmount } = renderPage()
          const hasNotFound = screen.queryByText('Project not found') !== null
          unmount()
          return hasNotFound === (hasError && !hasProject)
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
