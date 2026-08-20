import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseProjectsDataReturn } from '@/hooks/useProjectsData'
import { makeProject } from '../helpers/factories'


vi.mock('@/pages/projects/ProjectsSkeleton', () => ({
  ProjectsSkeleton: () => <div data-testid="projects-skeleton" />,
}))
vi.mock('@/pages/projects/ProjectFilters', () => ({
  ProjectFilters: () => <div data-testid="project-filters" />,
}))
const recordGridIds = vi.fn<(ids: readonly string[]) => void>()
vi.mock('@/pages/projects/ProjectGridView', () => ({
  ProjectGridView: ({ projects }: { projects: readonly { id: string }[] }) => {
    recordGridIds(projects.map((project) => project.id))
    return <div data-testid="project-grid-view" />
  },
}))
vi.mock('@/pages/projects/ProjectCreateDrawer', () => ({
  ProjectCreateDrawer: () => <div data-testid="project-create-drawer" />,
}))


const defaultHookReturn: UseProjectsDataReturn = {
  projects: [makeProject('proj-001')],
  filteredProjects: [makeProject('proj-001')],
  totalProjects: 1,
  loading: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultHookReturn }

const getProjectsData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useProjectsData', () => {
  const hookName = 'useProjectsData'
  return { [hookName]: () => getProjectsData() }
})

const recordSelectionIds = vi.fn<(ids: readonly string[]) => void>()
vi.mock('@/hooks/use-bulk-selection', () => ({
  useBulkSelection: (visibleIds: readonly string[]) => {
    recordSelectionIds(visibleIds)
    return {
      visibleSelected: new Set<string>(),
      selectedCount: 0,
      toggle: () => undefined,
      clear: () => undefined,
      confirmOpen: false,
      openConfirm: () => undefined,
      closeConfirm: () => undefined,
      deleting: false,
      runDelete: () => Promise.resolve(),
    }
  },
}))

import ProjectsPage from '@/pages/ProjectsPage'

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectsPage />
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
    recordSelectionIds.mockClear()
    recordGridIds.mockClear()
  })

  it('offers for deletion only the rows on the current page', () => {
    // Selection is held against what is on screen. Fed the whole filtered set
    // instead, a tick survives a page change, and the bulk bar's count and the
    // confirm dialog then cover rows the operator cannot see while agreeing to
    // delete them.
    const many = Array.from({ length: 60 }, (_, index) =>
      makeProject(`proj-${String(index).padStart(3, '0')}`),
    )
    hookReturn = {
      ...defaultHookReturn,
      projects: many,
      filteredProjects: many,
      totalProjects: many.length,
    }

    renderPage()

    // Pinned to the rows the grid actually rendered, not merely to "fewer than
    // all of them": a shorter list passes that check just as well when it is
    // empty, or when it is a different page than the one on screen.
    const offered = recordSelectionIds.mock.calls.at(-1)?.[0]
    const onScreen = recordGridIds.mock.calls.at(-1)?.[0]
    expect(onScreen).toBeDefined()
    expect(onScreen?.length).toBeGreaterThan(0)
    expect(onScreen?.length).toBeLessThan(many.length)
    expect(offered).toEqual(onScreen)
  })

  it('renders page heading', () => {
    renderPage()
    expect(screen.getByText('Projects')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, loading: true, totalProjects: 0, projects: [], filteredProjects: [] }
    renderPage()
    expect(screen.getByTestId('projects-skeleton')).toBeInTheDocument()
  })

  it('renders project count', () => {
    renderPage()
    // ListHeader shows (count) when filtered count matches total; falls back to "X of Y" when different.
    expect(screen.getByText('(1)')).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Connection lost' }
    renderPage()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('Connection lost')).toBeInTheDocument()
  })

  it('renders create project button', () => {
    renderPage()
    expect(screen.getByRole('button', { name: /New project/i })).toBeInTheDocument()
  })

  it('shows WebSocket disconnect warning when not connected', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderPage()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })
})
