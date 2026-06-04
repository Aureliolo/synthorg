import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { CustomRule } from '@/api/endpoints/custom-rules'

interface RulesStoreState {
  rules: readonly CustomRule[]
  loading: boolean
  error: string | null
  submitting: boolean
  fetchRules: () => Promise<void>
  fetchMetrics: () => Promise<void>
  deleteRule: (id: string) => Promise<boolean>
  toggleRule: (id: string) => Promise<void>
  createRule: () => Promise<unknown>
  updateRule: () => Promise<unknown>
}

let storeState: RulesStoreState

vi.mock('@/stores/custom-rules', () => ({
  useCustomRulesStore: Object.assign(
    (selector: (s: RulesStoreState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}))

const { default: CustomRulesPage } = await import('@/pages/CustomRulesPage')

function makeRule(overrides: Partial<CustomRule> = {}): CustomRule {
  return {
    id: 'r-1',
    name: 'High daily cost',
    description: 'Fires when daily spend is high',
    metric_path: 'budget.cost.daily_avg',
    comparator: 'gt',
    threshold: 100,
    severity: 'warning',
    target_altitudes: ['config_tuning'],
    enabled: true,
    created_at: '2026-04-19T00:00:00Z',
    updated_at: '2026-04-19T00:00:00Z',
    ...overrides,
  }
}

const defaultState: RulesStoreState = {
  rules: [],
  loading: false,
  error: null,
  submitting: false,
  fetchRules: vi.fn(async () => {}),
  fetchMetrics: vi.fn(async () => {}),
  deleteRule: vi.fn(() => Promise.resolve(true)),
  toggleRule: vi.fn(async () => {}),
  createRule: vi.fn(() => Promise.resolve(null)),
  updateRule: vi.fn(() => Promise.resolve(null)),
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CustomRulesPage />
    </MemoryRouter>,
  )
}

describe('CustomRulesPage', () => {
  beforeEach(() => {
    storeState = { ...defaultState }
    vi.clearAllMocks()
  })

  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Custom rules')).toBeInTheDocument()
  })

  it('renders the loading skeleton (not the empty copy) while loading with no rules', () => {
    storeState = { ...defaultState, loading: true }
    renderPage()
    expect(screen.getByText('Custom rules')).toBeInTheDocument()
    expect(screen.queryByText('No custom rules yet')).not.toBeInTheDocument()
  })

  it('renders the empty state when there are no rules', () => {
    renderPage()
    expect(screen.getByText('No custom rules yet')).toBeInTheDocument()
  })

  it('renders rule cards when rules are present', () => {
    storeState = { ...defaultState, rules: [makeRule({ name: 'High daily cost' })] }
    renderPage()
    expect(screen.getByText('High daily cost')).toBeInTheDocument()
    expect(screen.getByText('budget.cost.daily_avg')).toBeInTheDocument()
  })

  it('renders the error banner when error is set', () => {
    storeState = { ...defaultState, error: 'rule fetch failed' }
    renderPage()
    expect(screen.getByText('Could not load custom rules')).toBeInTheDocument()
    expect(screen.getByText('rule fetch failed')).toBeInTheDocument()
  })

  it('toggles a rule through the enable/disable button', () => {
    storeState = { ...defaultState, rules: [makeRule({ id: 'r-1', enabled: true })] }
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Disable rule' }))
    expect(storeState.toggleRule).toHaveBeenCalledWith('r-1')
  })

  it('deletes a rule after confirming the dialog', async () => {
    storeState = { ...defaultState, rules: [makeRule({ id: 'r-1' })] }
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Delete custom rule')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(storeState.deleteRule).toHaveBeenCalledWith('r-1'))
  })

  it('opens the create drawer from the New rule button', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /New rule/ }))
    expect(await screen.findByText('New custom rule')).toBeInTheDocument()
  })

  it('opens the edit drawer from a rule card', async () => {
    storeState = { ...defaultState, rules: [makeRule({ id: 'r-1', name: 'High daily cost' })] }
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(await screen.findByText('Edit · High daily cost')).toBeInTheDocument()
  })
})
