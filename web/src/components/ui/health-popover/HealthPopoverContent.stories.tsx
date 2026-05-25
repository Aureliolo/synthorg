import type { Meta, StoryObj } from '@storybook/react'
import { Dialog } from '@base-ui/react/dialog'
import { HealthPopoverContent } from './HealthPopoverContent'
import type { DerivedSubsystemStates } from './derive-subsystem-states'

const okStates: DerivedSubsystemStates = {
  apiState: 'ok',
  wsState: 'ok',
  persistenceState: 'ok',
  busState: 'ok',
  overallState: 'ok',
  wsDetail: undefined,
}

const degradedStates: DerivedSubsystemStates = {
  apiState: 'ok',
  wsState: 'degraded',
  persistenceState: 'ok',
  busState: 'down',
  overallState: 'down',
  wsDetail: 'auto-reconnecting',
}

const loadingStates: DerivedSubsystemStates = {
  apiState: 'loading',
  wsState: 'loading',
  persistenceState: 'loading',
  busState: 'loading',
  overallState: 'loading',
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
      <Dialog.Root open>
        <Dialog.Portal>
          <Dialog.Popup className="w-[640px] rounded-xl border border-border-bright bg-surface p-card">
            <Story />
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
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
const STORY_FETCHED_AT = STORY_FETCHED_AT

const OK_PAYLOAD = {
  status: 'ok' as const,
  persistence: true,
  message_bus: true,
  providers: true,
  telemetry: 'disabled' as const,
  version: '0.6.4',
  uptime_seconds: 847_200,
}

export const Default: Story = {
  args: {
    loadState: { state: 'ok', data: OK_PAYLOAD, fetchedAt: STORY_FETCHED_AT },
    states: okStates,
    fetchedAtLabel: '10:00 (just now)',
    onRefresh: () => undefined,
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

export const Loading: Story = {
  args: {
    loadState: { state: 'loading' },
    states: loadingStates,
    fetchedAtLabel: null,
    onRefresh: () => undefined,
  },
}

export const LoadError: Story = {
  args: {
    loadState: {
      state: 'error',
      message: 'Service unavailable',
      fetchedAt: STORY_FETCHED_AT,
    },
    states: { ...okStates, apiState: 'down', overallState: 'down' },
    fetchedAtLabel: '10:00 (just now)',
    onRefresh: () => undefined,
  },
}

export const Empty: Story = Loading
