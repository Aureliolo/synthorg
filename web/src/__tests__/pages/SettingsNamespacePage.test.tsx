import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import type { UseSettingsDataReturn } from '@/hooks/useSettingsData'
import type { SettingEntry } from '@/api/types/settings'

/**
 * The namespace filter lives in the URL, which is what lets another surface
 * link at one setting rather than at the page containing it: the health
 * dialog's memory card points here carrying the embedder key.
 */

function makeSetting(key: string, description: string): SettingEntry {
  return {
    definition: {
      namespace: 'memory',
      key,
      type: 'int',
      default: '1',
      description,
      group: 'Embedding',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: 1,
      max_value: 100,
    },
    value: '1',
    source: 'default',
    updated_at: null,
  }
}

const mockEntries: SettingEntry[] = [
  makeSetting('embedder_model', 'Embedding model agents recall through'),
  makeSetting('retention_days', 'How long a memory survives before pruning'),
]

const hookReturn: UseSettingsDataReturn = {
  schema: [],
  entries: mockEntries,
  loading: false,
  error: null,
  saving: false,
  saveError: null,
  isRefetching: false,
  wsConnected: true,
  wsSetupError: null,
  updateSetting: vi.fn().mockResolvedValue(mockEntries[0]),
  resetSetting: vi.fn().mockResolvedValue(undefined),
}

const getSettingsData = vi.fn(() => hookReturn)
// Dynamic key keeps Vitest's ESM mock hoisting from inlining the hook name,
// which would break the spy reference. Mirrors SettingsPage.test.
vi.mock('@/hooks/useSettingsData', () => {
  const hookName = 'useSettingsData'
  return { [hookName]: () => getSettingsData() }
})

vi.mock('@/stores/settings', () => ({
  useSettingsStore: vi.fn(
    (selector: (s: { savingKeys: ReadonlyMap<string, number> }) => unknown) =>
      selector({ savingKeys: new Map() }),
  ),
}))

import SettingsNamespacePage from '@/pages/SettingsNamespacePage'

function renderNamespace(entry: string) {
  const router = createMemoryRouter(
    [{ path: '/settings/:namespace', element: <SettingsNamespacePage /> }],
    { initialEntries: [entry] },
  )
  return { ...render(<RouterProvider router={router} />), router }
}

describe('SettingsNamespacePage URL-backed filter', () => {
  // The page renders RestartBanner, which fetches restart status in a mount
  // effect. Asserting synchronously returns before that settles, so the
  // store's set() lands after the test, outside act() and past the
  // active-handle gate.
  it('seeds the filter from the URL so a deep link lands on one row', async () => {
    renderNamespace('/settings/memory?q=embedder_model')

    expect(await screen.findByLabelText('Search settings')).toHaveValue('embedder_model')
    expect(screen.getByText('Embedder Model')).toBeInTheDocument()
    expect(screen.queryByText('Retention Days')).toBeNull()
  })

  it('shows the whole namespace when the URL carries no filter', async () => {
    renderNamespace('/settings/memory')

    expect(await screen.findByText('Embedder Model')).toBeInTheDocument()
    expect(screen.getByText('Retention Days')).toBeInTheDocument()
  })

  it('writes what the operator types back to the URL', async () => {
    const { router } = renderNamespace('/settings/memory')

    await userEvent.type(screen.getByLabelText('Search settings'), 'retention')

    await waitFor(() => {
      expect(router.state.location.search).toBe('?q=retention')
    })
  })

  it('drops the param rather than leaving an empty one behind', async () => {
    const { router } = renderNamespace('/settings/memory?q=embedder_model')

    await userEvent.click(screen.getByLabelText('Clear search'))

    await waitFor(() => {
      expect(router.state.location.search).toBe('')
    })
  })

  it('replaces history while filtering so Back leaves the page', async () => {
    // Pushing per keystroke would make Back walk the query letter by letter
    // instead of returning to wherever the operator came from.
    const { router } = renderNamespace('/settings/memory')

    await userEvent.type(screen.getByLabelText('Search settings'), 'retention')
    await waitFor(() => {
      expect(router.state.location.search).toBe('?q=retention')
    })

    expect(router.state.historyAction).toBe('REPLACE')
  })
})
