import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Dialog } from '@base-ui/react/dialog'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { HealthPopoverContent } from '@/components/ui/health-popover/HealthPopoverContent'
import { useProvidersStore } from '@/stores/providers'
import { useHealthStore } from '@/stores/health'
import type { DerivedSubsystemStates } from '@/components/ui/health-popover/derive-subsystem-states'
import type { LoadState } from '@/stores/health'

const INITIAL_PROVIDERS = useProvidersStore.getState()
const INITIAL_HEALTH = useHealthStore.getState()

/**
 * A card that names a fault has to offer the route that clears it.
 *
 * The gap these cover: the remedy used to render only on `down`, while the
 * faults an operator can actually fix themselves -- an unwired memory backend,
 * an absent backup schedule -- all read `degraded`. The card described the fix
 * in prose and then dead-ended.
 */

const FETCHED_AT = new Date('2099-01-01T10:00:00.000Z')

const HEALTHY_STATES: DerivedSubsystemStates = {
  costRecordingState: 'ok',
  costRecordingDetail: undefined,
  apiState: 'ok',
  wsState: 'ok',
  persistenceState: 'ok',
  busState: 'ok',
  providersState: 'ok',
  memoryState: 'ok',
  memoryDetail: 'sqlvector',
  memoryBackendState: 'durable',
  backupState: 'ok',
  backupDetail: undefined,
  withWebSocketState: 'ok',
  backendOnlyState: 'ok',
  wsDetail: undefined,
}

const LOAD_STATE: LoadState = {
  state: 'ok',
  data: {
    status: 'ok',
    persistence: true,
    message_bus: true,
    providers: true,
    telemetry: 'disabled',
    memory: { state: 'durable', backend: 'sqlvector', detail: null },
    backup: { state: 'wired', detail: null },
    cost_recording: { state: 'ok', dropped_records: 0, detail: null },
    version: '0.0.0-test',
    uptime_seconds: 1,
  },
  fetchedAt: FETCHED_AT,
}

function renderContent(
  overrides: Partial<DerivedSubsystemStates> = {},
  onDismiss: () => void = () => undefined,
) {
  const element = (
    <Dialog.Root open>
      <Dialog.Portal>
        <Dialog.Popup>
          <HealthPopoverContent
            loadState={LOAD_STATE}
            states={{ ...HEALTHY_STATES, ...overrides }}
            fetchedAtLabel="10:00 (just now)"
            onRefresh={() => undefined}
            onDismiss={onDismiss}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
  const router = createMemoryRouter([{ path: '*', element }], { initialEntries: ['/'] })
  return { ...render(<RouterProvider router={router} />), router }
}

describe('HealthPopoverContent remediation links', () => {
  // Restored before each test rather than after: these override single fields
  // on the real stores, and writing to them in teardown updates a tree React
  // has not unmounted yet.
  beforeEach(() => {
    useProvidersStore.setState(INITIAL_PROVIDERS, true)
    useHealthStore.setState(INITIAL_HEALTH, true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('points an unwired memory backend at the embedding-model row itself', () => {
    // `off` means no embedding model was ever named, which is the operator's to
    // fix, so the link carries the key the settings filter matches on.
    renderContent({ memoryState: 'degraded', memoryBackendState: 'off' })

    expect(screen.getByRole('link', { name: 'Choose an embedding model' })).toHaveAttribute(
      'href',
      '/settings/memory?q=embedder_model',
    )
  })

  it('offers memory settings, not the embedder row, when a wired backend is unreachable', () => {
    // A backend that is wired and not answering is not an unchosen model, so
    // naming the embedder here would send the operator to change a setting that
    // is not the fault.
    renderContent({ memoryState: 'down', memoryBackendState: 'unreachable' })

    expect(screen.getByRole('link', { name: 'Open memory settings' })).toHaveAttribute(
      'href',
      '/settings/memory',
    )
    expect(screen.queryByRole('link', { name: 'Choose an embedding model' })).toBeNull()
  })

  it('renders the remedy on a degraded card, not only on a down one', () => {
    // The regression this whole surface existed to have: absent backups are
    // degraded by design (the deployment serves, it has lost a recovery point),
    // and that is exactly when the operator can act.
    renderContent({ backupState: 'degraded' })

    expect(screen.getByRole('link', { name: 'Configure backups' })).toHaveAttribute(
      'href',
      '/admin/backups',
    )
  })

  it('routes an unreachable provider at the providers page', () => {
    renderContent({ providersState: 'down' })

    expect(screen.getByRole('link', { name: 'Review providers' })).toHaveAttribute(
      'href',
      '/providers',
    )
  })

  it('offers no remedy while every subsystem is healthy', () => {
    renderContent()

    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })

  it('offers no remedy before the first snapshot settles', () => {
    // Nothing is known to be wrong yet, and routing an operator at a fix for a
    // subsystem that has not reported would send them to change a healthy
    // setting.
    renderContent({
      memoryState: 'loading',
      memoryBackendState: null,
      backupState: 'unknown',
      providersState: 'unknown',
    })

    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })

  it('dismisses the dialog when a remedy is followed', async () => {
    // Otherwise the modal stays mounted over the page it just sent the
    // operator to.
    const onDismiss = vi.fn()
    renderContent({ memoryState: 'degraded', memoryBackendState: 'off' }, onDismiss)

    await userEvent.click(screen.getByRole('link', { name: 'Choose an embedding model' }))

    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('offers a recheck on an unhealthy provider card', async () => {
    // The gap this covers: nothing re-derived provider health between probe
    // cycles, so an operator who had just fixed a provider had no way to say
    // "look again" short of opening it and re-saving it.
    const recheckAllHealth = vi.fn(() => Promise.resolve())
    // One field overridden on the real store rather than a whole replacement
    // snapshot: anything else the tree reads would otherwise be undefined and
    // hide a genuine mismatch.
    useProvidersStore.setState({ recheckAllHealth })
    renderContent({ providersState: 'down' })

    await userEvent.click(screen.getByRole('button', { name: 'Recheck now' }))

    expect(recheckAllHealth).toHaveBeenCalledOnce()
  })

  it('refreshes the health snapshot the card itself renders from', async () => {
    // The sweep writes the providers store; this card reads the health
    // snapshot. Without the refetch the dialog offering the action would keep
    // showing the state it just corrected.
    const recheckAllHealth = vi.fn(() => Promise.resolve())
    const fetchHealth = vi.fn(() => Promise.resolve())
    useProvidersStore.setState({ recheckAllHealth })
    useHealthStore.setState({ fetchHealth })
    renderContent({ providersState: 'down' })

    await userEvent.click(screen.getByRole('button', { name: 'Recheck now' }))

    expect(fetchHealth).toHaveBeenCalledOnce()
  })

  it('disables the recheck while a sweep is already in flight', () => {
    // Without this the button re-fires, and each press bills a completion
    // against every configured provider.
    useProvidersStore.setState({ recheckingAllHealth: true })
    renderContent({ providersState: 'down' })

    expect(screen.getByRole('button', { name: 'Checking...' })).toBeDisabled()
  })

  it('offers no recheck while providers are healthy', () => {
    renderContent()

    expect(screen.queryByRole('button', { name: 'Recheck now' })).toBeNull()
  })

  it('keeps recheck a button rather than a navigation link', () => {
    // It acts on the spot; an anchor would promise a destination it has not
    // got, and would be the wrong thing to open in a new tab.
    renderContent({ providersState: 'down' })

    expect(screen.getByRole('button', { name: 'Recheck now' }).tagName).toBe('BUTTON')
  })

  it('keeps the remedy a link rather than giving it button semantics', () => {
    // The anchor is the point: open in a new tab, copy the address, and a
    // destination announced as a destination.
    renderContent({ memoryState: 'degraded', memoryBackendState: 'off' })

    const link = screen.getByRole('link', { name: 'Choose an embedding model' })
    expect(link.tagName).toBe('A')
    expect(link).not.toHaveAttribute('role')
  })
})
