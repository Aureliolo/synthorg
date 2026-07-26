import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseOntologyDataReturn } from '@/hooks/useOntologyData'
import type { DriftReportResponse, EntityResponse } from '@/api/types/ontology'

// ── Mock data ──────────────────────────────────────────────

const mockEntity: EntityResponse = {
  name: 'Task',
  tier: 'core',
  source: 'auto',
  definition: 'A unit of work assigned to an agent',
  fields: [{ name: 'id', type_hint: 'str', description: 'Unique ID' }],
  constraints: ['must have an owner'],
  disambiguation: 'Not a calendar event',
  relationships: [],
  created_by: 'system',
  created_at: '2026-04-01T00:00:00Z',
  updated_at: '2026-04-01T00:00:00Z',
}

const mockDriftReport: DriftReportResponse = {
  entity_name: 'Task',
  divergence_score: 0.35,
  divergent_agents: [
    { agent_id: 'agent-1', divergence_score: 0.35, details: 'Keyword overlap: 65.0%' },
  ],
  canonical_version: 2,
  recommendation: 'notify',
}

const defaultHookReturn: UseOntologyDataReturn = {
  entities: [mockEntity],
  filteredEntities: [mockEntity],
  totalEntities: 1,
  entitiesLoading: false,
  entitiesError: null,
  driftReports: [mockDriftReport],
  driftLoading: false,
  driftError: null,
  tierFilter: 'all',
  searchQuery: '',
  selectedEntity: null,
  coreCount: 1,
  userCount: 0,
}

// ── Hook mock ──────────────────────────────────────────────

let hookReturn: UseOntologyDataReturn
const getOntologyData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useOntologyData', () => ({
  useOntologyData: () => getOntologyData(),
}))

// Must import page AFTER vi.mock
import OntologyPage from '@/pages/OntologyPage'

function renderOntology() {
  return render(
    <MemoryRouter>
      <OntologyPage />
    </MemoryRouter>,
  )
}

// ── Tests ──────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  hookReturn = { ...defaultHookReturn }
})

describe('OntologyPage', () => {
  it('renders page heading', () => {
    renderOntology()
    expect(screen.getByText('Ontology')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, entities: [], filteredEntities: [], totalEntities: 0, entitiesLoading: true, coreCount: 0, userCount: 0 }
    renderOntology()
    // Skeleton renders -- heading "Ontology" should NOT be present
    expect(screen.queryByText('Ontology')).not.toBeInTheDocument()
  })

  it('does not show skeleton when loading but data exists', () => {
    hookReturn = { ...defaultHookReturn, entitiesLoading: true }
    renderOntology()
    // Heading visible when data exists even during loading
    expect(screen.getByText('Ontology')).toBeInTheDocument()
  })

  it('renders entity catalog with entity card', () => {
    renderOntology()
    // "Task" appears in both entity card and drift table -- use getAllByText
    expect(screen.getAllByText('Task').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('A unit of work assigned to an agent')).toBeInTheDocument()
  })

  it('renders drift monitor section', () => {
    renderOntology()
    expect(screen.getByText('Drift Monitor')).toBeInTheDocument()
  })

  it('renders drift report data', () => {
    renderOntology()
    // Divergence score rendered as percentage
    expect(screen.getByText('35%')).toBeInTheDocument()
  })

  it('renders empty state when no entities', () => {
    hookReturn = {
      ...defaultHookReturn,
      entities: [],
      filteredEntities: [],
      totalEntities: 0,
      coreCount: 0,
      userCount: 0,
    }
    renderOntology()
    expect(screen.getByText(/no entities/i)).toBeInTheDocument()
  })

  it('renders error state for entities', () => {
    hookReturn = { ...defaultHookReturn, entitiesError: 'Connection failed' }
    renderOntology()
    expect(screen.getByText(/connection failed/i)).toBeInTheDocument()
  })

  it('renders error state for drift', () => {
    hookReturn = { ...defaultHookReturn, driftError: 'Drift fetch failed' }
    renderOntology()
    expect(screen.getByText(/drift fetch failed/i)).toBeInTheDocument()
  })
})
