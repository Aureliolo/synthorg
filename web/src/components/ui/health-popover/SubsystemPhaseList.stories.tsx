import type { Meta, StoryObj } from '@storybook/react'
import { SubsystemPhaseList } from './SubsystemPhaseList'

const meta = {
  title: 'Overlays/HealthPopover/SubsystemPhaseList',
  component: SubsystemPhaseList,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <div className="w-[36rem]">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof SubsystemPhaseList>

export default meta
type Story = StoryObj<typeof meta>

export const AllActive: Story = {
  args: {
    error: null,
    subsystems: [
      { name: 'charter_engine', phase: 'active', detail: null, waiting_on: [] },
      { name: 'initiative_integrate', phase: 'active', detail: null, waiting_on: [] },
      { name: 'project_rollup_service', phase: 'active', detail: null, waiting_on: [] },
    ],
  },
}

// The shape a real deployment is usually in: most up, a few naming their own
// condition. Each row has to say which, without the operator opening the API.
export const Mixed: Story = {
  args: {
    error: null,
    subsystems: [
      { name: 'charter_engine', phase: 'active', detail: null, waiting_on: [] },
      {
        name: 'memory_backend',
        phase: 'blocked',
        detail: 'memory.embedder_model is unset',
        waiting_on: [],
      },
      {
        name: 'conversational_actor',
        phase: 'waiting',
        detail: null,
        waiting_on: ['mcp_self_consumer'],
      },
      {
        name: 'risk_override_service',
        phase: 'failed',
        detail: 'activation raised: no signing key configured',
        waiting_on: [],
      },
      { name: 'telemetry', phase: 'disabled', detail: null, waiting_on: [] },
    ],
  },
}

// Nothing read yet: an empty list is not evidence that nothing is declared.
export const NotReadYet: Story = {
  args: {
    error: null,
    subsystems: [],
  },
}

export const ReadFailed: Story = {
  args: {
    error: 'Request failed with status code 503',
    subsystems: [],
  },
}
