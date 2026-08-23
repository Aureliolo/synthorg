import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router'
import type { UseAgentDetailDataReturn } from '@/hooks/useAgentDetailData'
import { makeAgent } from '../helpers/factories'


vi.mock('@/pages/agents/AgentDetailSkeleton', () => ({
  AgentDetailSkeleton: () => <div data-testid="agent-detail-skeleton" />,
}))
interface MockAgentProp {
  agent: { name: string }
}
vi.mock('@/pages/agents/AgentIdentityHeader', () => ({
  AgentIdentityHeader: ({ agent }: MockAgentProp) => (
    <div data-testid="identity-header">{agent.name}</div>
  ),
}))
vi.mock('@/pages/agents/ProseInsight', () => ({
  ProseInsight: () => <div data-testid="prose-insight" />,
}))
vi.mock('@/pages/agents/PerformanceMetrics', () => ({
  PerformanceMetrics: () => <div data-testid="performance-metrics" />,
}))
vi.mock('@/pages/agents/ToolBadges', () => ({
  ToolBadges: () => <div data-testid="tool-badges" />,
}))
vi.mock('@/pages/agents/CareerTimeline', () => ({
  CareerTimeline: () => <div data-testid="career-timeline" />,
}))
vi.mock('@/pages/agents/TaskHistory', () => ({
  TaskHistory: () => <div data-testid="task-history" />,
}))
vi.mock('@/pages/agents/ActivityLog', () => ({
  ActivityLog: () => <div data-testid="activity-log" />,
}))

const defaultHookReturn: UseAgentDetailDataReturn = {
  agent: makeAgent('alice'),
  performance: null,
  health: null,
  performanceCards: [],
  insights: [],
  agentTasks: [],
  activity: [],
  activityTotal: 0,
  careerHistory: [],
  loading: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
  fetchMoreActivity: vi.fn(),
}

let hookReturn = { ...defaultHookReturn }

const getDetailData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useAgentDetailData', () => {
  const hookName = 'useAgentDetailData'
  return { [hookName]: () => getDetailData() }
})

// Static import: vi.mock is hoisted so the mock is applied before import
import AgentDetailPage from '@/pages/AgentDetailPage'

function renderDetail(id = 'alice') {
  return render(
    <MemoryRouter initialEntries={[`/agents/${id}`]}>
      <Routes>
        <Route path="/agents/:agentId" element={<AgentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AgentDetailPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn, fetchMoreActivity: vi.fn() }
  })

  it('renders loading skeleton when loading with no agent', () => {
    hookReturn = { ...defaultHookReturn, loading: true, agent: null }
    renderDetail()
    expect(screen.getByTestId('agent-detail-skeleton')).toBeInTheDocument()
  })

  it('renders not-found message when no agent and error is set', () => {
    hookReturn = {
      ...defaultHookReturn,
      agent: null,
      error: 'Agent missing',
    }
    renderDetail()
    expect(screen.getByText('Agent not found')).toBeInTheDocument()
  })

  it('renders error message from hook when no agent', () => {
    hookReturn = { ...defaultHookReturn, agent: null, error: 'Agent does not exist' }
    renderDetail()
    expect(screen.getByText('Agent does not exist')).toBeInTheDocument()
  })

  it('shows error banner when error exists and agent exists', () => {
    hookReturn = { ...defaultHookReturn, error: 'Partial failure' }
    renderDetail()
    expect(screen.getByText('Partial failure')).toBeInTheDocument()
    expect(screen.getByTestId('identity-header')).toBeInTheDocument()
  })

  it('shows WebSocket disconnect warning when not connected', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderDetail()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })

  it('shows custom wsSetupError message when provided', () => {
    hookReturn = {
      ...defaultHookReturn,
      wsConnected: false,
      wsSetupError: 'WebSocket auth failed',
    }
    renderDetail()
    expect(screen.getByText('WebSocket auth failed')).toBeInTheDocument()
  })

  it('hides disconnect warning when loading', () => {
    hookReturn = {
      ...defaultHookReturn,
      wsConnected: false,
      loading: true,
    }
    renderDetail()
    expect(
      screen.queryByText(/disconnected/i),
    ).not.toBeInTheDocument()
  })

  it('renders agent identity header section', () => {
    renderDetail()
    expect(screen.getByTestId('identity-header')).toBeInTheDocument()
    // `alice` appears in both the breadcrumb and the identity header; accept either.
    expect(screen.getAllByText('alice').length).toBeGreaterThanOrEqual(1)
  })

  it('renders activity log section', () => {
    renderDetail()
    expect(screen.getByTestId('activity-log')).toBeInTheDocument()
  })
})
