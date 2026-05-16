import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { UseProvidersDataReturn } from '@/hooks/useProvidersData'
import type { ProviderWithName } from '@/utils/providers'

// Mutable hook return for test control
let hookReturn: UseProvidersDataReturn

const getProvidersData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useProvidersData', () => {
  const hookName = 'useProvidersData'
  return { [hookName]: () => getProvidersData() }
})

// Must import after mock
const { default: ProvidersPage } = await import('@/pages/ProvidersPage')

function makeProvider(name: string): ProviderWithName {
  return {
    name,
    driver: 'litellm',
    litellm_provider: 'test',
    auth_type: 'api_key',
    base_url: null,
    models: [],
    has_api_key: true,
    has_oauth_credentials: false,
    has_custom_header: false,
    has_subscription_token: false,
    tos_accepted_at: null,
    oauth_token_url: null,
    oauth_client_id: null,
    oauth_scope: null,
    custom_header_name: null,
    preset_name: null,
    supports_model_pull: false,
    supports_model_delete: false,
    supports_model_config: false,
  }
}

const defaultReturn: UseProvidersDataReturn = {
  providers: [],
  filteredProviders: [],
  healthMap: {},
  loading: false,
  error: null,
  isRefetching: false,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ProvidersPage />
    </MemoryRouter>,
  )
}

describe('ProvidersPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultReturn }
    vi.clearAllMocks()
  })

  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Providers')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultReturn, loading: true }
    renderPage()
    expect(screen.getByLabelText('Loading providers')).toBeInTheDocument()
  })

  it('renders empty state when no providers', () => {
    renderPage()
    expect(screen.getByText('No providers configured')).toBeInTheDocument()
  })

  it('renders provider cards when data is available', () => {
    const providers = [makeProvider('anthropic'), makeProvider('openai')]
    hookReturn = { ...defaultReturn, filteredProviders: providers, providers }
    renderPage()
    expect(screen.getByText('anthropic')).toBeInTheDocument()
    expect(screen.getByText('openai')).toBeInTheDocument()
  })

  it('renders error banner when error is set', () => {
    hookReturn = { ...defaultReturn, error: 'Network error' }
    renderPage()
    expect(screen.getByText('Network error')).toBeInTheDocument()
  })

  it('renders Add Provider button', () => {
    renderPage()
    // Multiple "Add Provider" elements may exist (header button + empty state CTA)
    const buttons = screen.getAllByText('Add Provider')
    expect(buttons.length).toBeGreaterThanOrEqual(1)
  })
})
