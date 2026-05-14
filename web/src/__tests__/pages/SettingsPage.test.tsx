import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, Link, RouterProvider } from 'react-router'
import type { UseSettingsDataReturn } from '@/hooks/useSettingsData'
import type { SettingEntry } from '@/api/types/settings'

function makeSetting(overrides: Partial<SettingEntry['definition']> & { value?: string; source?: SettingEntry['source'] } = {}): SettingEntry {
  const {
    value = '3001',
    source = 'default',
    ...defOverrides
  } = overrides
  return {
    definition: {
      namespace: 'api',
      key: 'server_port',
      type: 'int',
      default: '3001',
      description: 'Server bind port',
      group: 'Server',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: 1,
      max_value: 65535,
      yaml_path: 'api.server.port',
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

const mockEntries: SettingEntry[] = [
  makeSetting(),
  makeSetting({
    key: 'rate_limit_max_requests',
    description: 'Maximum requests per time window',
    group: 'Rate Limiting',
    min_value: 1,
    max_value: 10000,
    value: '100',
  }),
  makeSetting({
    namespace: 'budget',
    key: 'total_monthly',
    type: 'float',
    description: 'Monthly budget limit',
    group: 'Limits',
    min_value: 0,
    max_value: null,
    yaml_path: 'budget.total_monthly',
    value: '100.0',
  }),
  makeSetting({
    namespace: 'security',
    key: 'enabled',
    type: 'bool',
    description: 'Master switch for the security subsystem',
    group: 'General',
    min_value: null,
    max_value: null,
    yaml_path: 'security.enabled',
    value: 'true',
  }),
  makeSetting({
    key: 'api_prefix',
    description: 'URL prefix for all API routes',
    group: 'Server',
    level: 'advanced',
    min_value: null,
    max_value: null,
    value: '/api/v1',
  }),
]

const mockUpdateSetting = vi.fn().mockResolvedValue({
  definition: { namespace: 'api', key: 'server_port', type: 'int', default: '3001', description: 'Server bind port', group: 'Server', level: 'basic', sensitive: false, restart_required: false, enum_values: [], validator_pattern: null, min_value: 1, max_value: 65535, yaml_path: 'api.server.port' },
  value: '3001', source: 'db', updated_at: null,
})
const mockResetSetting = vi.fn().mockResolvedValue(undefined)

const defaultHookReturn: UseSettingsDataReturn = {
  schema: [],
  entries: mockEntries,
  loading: false,
  error: null,
  saving: false,
  saveError: null,
  wsConnected: true,
  wsSetupError: null,
  updateSetting: mockUpdateSetting,
  resetSetting: mockResetSetting,
}

let hookReturn = { ...defaultHookReturn }

const getSettingsData = vi.fn(() => hookReturn)
// Dynamic key avoids Vitest's ESM mock hoisting from
// inlining the hook name, which breaks the spy reference
// to getSettingsData. See: useSettingsData + getSettingsData.
vi.mock('@/hooks/useSettingsData', () => {
  const hookName = 'useSettingsData'
  return { [hookName]: () => getSettingsData() }
})

// Mock the settings store for savingKeys
vi.mock('@/stores/settings', () => ({
  useSettingsStore: vi.fn(
    (selector: (s: { savingKeys: ReadonlyMap<string, number> }) => unknown) =>
      selector({ savingKeys: new Map() }),
  ),
}))

// Swap-in hook result. Tests mutate `guardState` to flip confirmOpen on and
// off; the mock factory itself stays stable because vi.mock is hoisted.
const guardState = {
  confirmOpen: false,
  proceed: vi.fn(),
  cancel: vi.fn(),
  message: 'Discard unsaved changes?',
  hasDraft: false,
  restoreDraft: () => null,
  discardDraft: vi.fn(),
}
vi.mock('@/hooks/use-unsaved-changes-guard', () => ({
  useUnsavedChangesGuard: () => guardState,
}))

import SettingsPage from '@/pages/SettingsPage'

function renderSettings() {
  // SettingsPage uses useUnsavedChangesGuard (calls useBlocker internally),
  // which requires a data router rather than a plain MemoryRouter.
  const router = createMemoryRouter(
    [{ path: '/', element: <SettingsPage /> }],
    { initialEntries: ['/'] },
  )
  return render(<RouterProvider router={router} />)
}

describe('SettingsPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
  })

  it('renders page heading', () => {
    renderSettings()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, loading: true, entries: [] }
    renderSettings()
    expect(screen.getByLabelText('Loading settings')).toBeInTheDocument()
  })

  it('does not show skeleton when loading but data already exists', () => {
    hookReturn = { ...defaultHookReturn, loading: true }
    renderSettings()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.queryByLabelText('Loading settings')).not.toBeInTheDocument()
  })

  it('renders namespace sections', () => {
    renderSettings()
    // Namespace names appear in both the tab bar and section headers
    expect(screen.getAllByText('Server').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Budget').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Security').length).toBeGreaterThanOrEqual(1)
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Connection lost' }
    renderSettings()
    expect(screen.getByRole('alert')).toHaveTextContent('Connection lost')
  })

  it('shows WebSocket disconnect warning when not connected', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderSettings()
    expect(screen.getByText(/data may be stale/i)).toBeInTheDocument()
  })

  it('renders search input', () => {
    renderSettings()
    expect(screen.getByLabelText('Search settings')).toBeInTheDocument()
  })

  it('renders Advanced toggle', () => {
    renderSettings()
    expect(screen.getByText('Advanced')).toBeInTheDocument()
  })

  it('renders Code view toggle', () => {
    renderSettings()
    expect(screen.getByText('Code')).toBeInTheDocument()
  })

  it('hides advanced settings in basic mode', () => {
    renderSettings()
    // api_prefix is level=advanced, should not appear
    expect(screen.queryByText('Api Prefix')).not.toBeInTheDocument()
  })

  it('shows advanced settings when advanced mode is enabled', () => {
    localStorage.setItem('settings_show_advanced', 'true')
    renderSettings()
    expect(screen.getByText('Api Prefix')).toBeInTheDocument()
  })

  it('shows empty state when no entries match', () => {
    hookReturn = { ...defaultHookReturn, entries: [] }
    renderSettings()
    expect(screen.getByText('No settings available')).toBeInTheDocument()
  })

  it('shows custom wsSetupError message when wsSetupError is set', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false, wsSetupError: 'Auth token expired' }
    renderSettings()
    expect(screen.getByText('Auth token expired')).toBeInTheDocument()
  })

  describe('unsaved-changes guard', () => {
    // These tests exercise the Page-level wiring: SettingsPage passes
    // `confirmOpen` / `proceed` / `cancel` from useUnsavedChangesGuard to
    // its ConfirmDialog. The guard hook itself has its own test suite that
    // covers the dirty-state / blocker / beforeunload interactions; these
    // tests would otherwise duplicate that work and brittle on form
    // internals.

    function renderWithTwoRoutes() {
      const router = createMemoryRouter(
        [
          {
            path: '/',
            element: (
              <>
                <Link to="/other">Go elsewhere</Link>
                <SettingsPage />
              </>
            ),
          },
          { path: '/other', element: <div>Other route</div> },
        ],
        { initialEntries: ['/'] },
      )
      return render(<RouterProvider router={router} />)
    }

    beforeEach(() => {
      guardState.confirmOpen = true
      guardState.proceed = vi.fn()
      guardState.cancel = vi.fn()
    })

    afterEach(() => {
      guardState.confirmOpen = false
    })

    it('opens the ConfirmDialog when the guard reports confirmOpen=true, and Cancel calls the hook cancel handler', async () => {
      const user = userEvent.setup()
      renderWithTwoRoutes()
      const dialog = await screen.findByRole('alertdialog')
      expect(dialog).toBeInTheDocument()
      await user.click(within(dialog).getByRole('button', { name: /cancel|keep editing/i }))
      expect(guardState.cancel).toHaveBeenCalled()
      expect(guardState.proceed).not.toHaveBeenCalled()
    })

    it('invokes the hook proceed handler when the user confirms discard', async () => {
      // Base UI's AlertDialog also fires `onOpenChange(false)` when the
      // dialog closes, which SettingsPage wires to cancel() as a reset
      // path. cancel() is a no-op once proceed() has transitioned the
      // blocker, so we only assert proceed() here.
      const user = userEvent.setup()
      renderWithTwoRoutes()
      const dialog = await screen.findByRole('alertdialog')
      await user.click(within(dialog).getByRole('button', { name: /discard|leave|continue/i }))
      expect(guardState.proceed).toHaveBeenCalledTimes(1)
    })
  })
})
