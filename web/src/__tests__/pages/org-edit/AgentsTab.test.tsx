import { render, screen } from '@testing-library/react'
import { AgentsTab, type AgentsTabProps } from '@/pages/org-edit/AgentsTab'
import type { CompanyConfig } from '@/api/types/org'
import { makeAgent, makeDepartment } from '../../helpers/factories'

const noopAsync = vi.fn().mockResolvedValue(undefined)
const noopRollback = vi.fn().mockReturnValue(() => {})

/** Config with known agents for deterministic assertions. */
const knownConfig: CompanyConfig = {
  company_name: 'Test Corp',
  agents: [
    makeAgent('alice', { department: 'engineering', role: 'Lead Developer' }),
    makeAgent('bob', { department: 'engineering', role: 'Developer' }),
    makeAgent('carol', { department: 'product', role: 'Product Manager' }),
  ],
  departments: [
    makeDepartment('engineering'),
    makeDepartment('product'),
  ],
}

function renderTab(overrides?: Partial<AgentsTabProps>) {
  const props: AgentsTabProps = {
    config: knownConfig,
    saving: false,
    onCreateAgent: noopAsync,
    onUpdateAgent: noopAsync,
    onDeleteAgent: noopAsync,
    onReorderAgents: noopAsync,
    optimisticReorderAgents: noopRollback,
    ...overrides,
  }
  return render(<AgentsTab {...props} />)
}

describe('AgentsTab', () => {
  beforeEach(() => vi.resetAllMocks())

  it('renders global empty state when no agents and no departments', () => {
    renderTab({ config: { ...knownConfig, agents: [], departments: [] } })
    expect(screen.getByText('No agents')).toBeInTheDocument()
  })

  it('renders per-department empty messages when agents are empty but departments exist', () => {
    renderTab({ config: { ...knownConfig, agents: [] } })
    const msgs = screen.getAllByText('No agents in this department')
    expect(msgs.length).toBe(knownConfig.departments.length)
  })

  it('renders Add Agent button', () => {
    renderTab()
    expect(screen.getByText('Add Agent')).toBeInTheDocument()
  })

  it('renders agent cards grouped by department', () => {
    renderTab()
    // Engineering department header
    expect(screen.getByText('Engineering')).toBeInTheDocument()
    // Product department header
    expect(screen.getByText('Product')).toBeInTheDocument()
  })

  it('renders agent names', () => {
    renderTab()
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.getByText('carol')).toBeInTheDocument()
  })

  it('renders agent count per department', () => {
    renderTab()
    expect(screen.getByText('2 agents')).toBeInTheDocument()
    expect(screen.getByText('1 agent')).toBeInTheDocument()
  })

  it('renders empty state when config is null', () => {
    renderTab({ config: null })
    expect(screen.getByText('No agents')).toBeInTheDocument()
  })
})
