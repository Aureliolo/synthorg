import type { Meta, StoryObj } from '@storybook/react'
import { Dialog } from '@base-ui/react/dialog'
import { MemoryRouter } from 'react-router'
import { HealthPopoverContent } from './HealthPopoverContent'
import type { DerivedSubsystemStates } from './derive-subsystem-states'

const okStates: DerivedSubsystemStates = {
  apiState: 'ok',
  wsState: 'ok',
  persistenceState: 'ok',
  persistenceDetail: 'sqlite',
  busState: 'ok',
  providersState: 'ok',
  memoryState: 'ok',
  memoryDetail: 'sqlvector',
  memoryBackendState: 'durable',
  backupState: 'ok',
  backupDetail: undefined,
  costRecordingState: 'ok',
  costRecordingDetail: undefined,
  withWebSocketState: 'ok',
  backendOnlyState: 'ok',
  wsDetail: undefined,
}

// A down message bus behind a healthy API: the hero must say a subsystem is
// unreachable, not that the backend is, while the API card beside it reads
// operational and the panel shows data that backend just returned.
const degradedStates: DerivedSubsystemStates = {
  apiState: 'ok',
  wsState: 'degraded',
  persistenceState: 'ok',
  persistenceDetail: 'sqlite',
  busState: 'down',
  providersState: 'ok',
  memoryState: 'degraded',
  memoryDetail: 'no embedding model chosen; agents will run without recall',
  memoryBackendState: 'off',
  backupState: 'degraded',
  backupDetail: 'pg_dump is not available on PATH',
  costRecordingState: 'degraded',
  costRecordingDetail: '3 cost records in a row failed to persist',
  withWebSocketState: 'down',
  backendOnlyState: 'down',
  wsDetail: 'auto-reconnecting',
}

const loadingStates: DerivedSubsystemStates = {
  apiState: 'loading',
  wsState: 'loading',
  persistenceState: 'loading',
  persistenceDetail: 'sqlite',
  busState: 'loading',
  providersState: 'loading',
  memoryState: 'loading',
  memoryDetail: undefined,
  memoryBackendState: null,
  backupState: 'loading',
  backupDetail: undefined,
  costRecordingState: 'ok',
  costRecordingDetail: undefined,
  withWebSocketState: 'loading',
  backendOnlyState: 'loading',
  wsDetail: undefined,
}

const meta = {
  title: 'Overlays/HealthPopover/HealthPopoverContent',
  component: HealthPopoverContent,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Dialog.Root open>
          <Dialog.Portal>
            <Dialog.Popup className="w-full max-w-3xl rounded-xl border border-border-bright bg-surface p-card">
              <Story />
            </Dialog.Popup>
          </Dialog.Portal>
        </Dialog.Root>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof HealthPopoverContent>

export default meta
type Story = StoryObj<typeof meta>

// Frozen reference date used across stories so visual-regression snapshots
// (Chromatic / Playwright) are deterministic from run to run. Using
// ``new Date()`` would re-snapshot every day; using a stable far-future
// date keeps the "fetchedAt" timestamp visible in stories without
// requiring snapshot refresh after every clock tick.
const STORY_FETCHED_AT: Date = new Date('2099-01-01T10:00:00.000Z')

const OK_PAYLOAD = {
  status: 'ok' as const,
  persistence: true,
  persistence_backend: 'sqlite',
  message_bus: true,
  providers: 'ok' as const,
  telemetry: 'disabled' as const,
  memory: { state: 'durable' as const, backend: 'sqlvector', detail: null },
  backup: { state: 'wired' as const, detail: null },
  cost_recording: { state: 'ok' as const, dropped_records: 0, detail: null },
  version: '0.6.4',
  uptime_seconds: 847_200,
}

const DECLARED_SUBSYSTEMS = [
  { name: 'charter_engine', phase: 'active' as const, detail: null, waiting_on: [] },
  { name: 'initiative_evaluate', phase: 'active' as const, detail: null, waiting_on: [] },
  {
    name: 'conversational_actor',
    phase: 'waiting' as const,
    detail: null,
    waiting_on: ['mcp_self_consumer'],
  },
]

export const Default: Story = {
  args: {
    loadState: { state: 'ok', data: OK_PAYLOAD, fetchedAt: STORY_FETCHED_AT },
    states: okStates,
    subsystems: DECLARED_SUBSYSTEMS,
    subsystemsError: null,
    fetchedAtLabel: '10:00 (just now)',
    onRefresh: () => undefined,
    onDismiss: () => undefined,
  },
}

export const Degraded: Story = {
  args: {
    ...Default.args,
    loadState: {
      state: 'ok',
      data: { ...OK_PAYLOAD, status: 'unavailable', message_bus: false },
      fetchedAt: STORY_FETCHED_AT,
    },
    states: degradedStates,
  },
}

// An unreachable provider is the one fault an operator commonly fixes outside
// the dashboard, so the card carries an action that re-derives the verdict as
// well as the link that navigates to it. The overall status stays `ok`: the
// backend reports provider reachability but never gates readiness on it, so a
// down provider alongside healthy dependencies is exactly what a real payload
// looks like.
export const ProvidersUnreachable: Story = {
  args: {
    ...Default.args,
    loadState: {
      state: 'ok',
      data: { ...OK_PAYLOAD, providers: 'down' },
      fetchedAt: STORY_FETCHED_AT,
    },
    states: { ...okStates, providersState: 'down' },
  },
}

// The state a boolean could not carry, and the reason the field is not one:
// some calls are failing and the rest are being served, which reads neither
// like an outage nor like everything being fine.
export const ProvidersDegraded: Story = {
  args: {
    ...Default.args,
    loadState: {
      state: 'ok',
      data: { ...OK_PAYLOAD, providers: 'degraded' },
      fetchedAt: STORY_FETCHED_AT,
    },
    states: { ...okStates, providersState: 'degraded' },
  },
}

export const Loading: Story = {
  args: {
    loadState: { state: 'loading', previous: null },
    states: loadingStates,
    subsystems: [],
    subsystemsError: null,
    fetchedAtLabel: null,
    onRefresh: () => undefined,
    onDismiss: () => undefined,
  },
}

export const LoadError: Story = {
  args: {
    loadState: {
      state: 'error',
      message: 'Service unavailable',
      fetchedAt: STORY_FETCHED_AT,
    },
    states: { ...okStates, apiState: 'down', withWebSocketState: 'down', backendOnlyState: 'down' },
    subsystems: [],
    subsystemsError: 'Request failed with status code 503',
    fetchedAtLabel: '10:00 (just now)',
    onRefresh: () => undefined,
    onDismiss: () => undefined,
  },
}

export const Empty: Story = Loading
