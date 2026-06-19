import type { Meta, StoryObj } from '@storybook/react'
import { ProgressIndicator } from './progress-indicator'

const meta = {
  title: 'Feedback/ProgressIndicator',
  component: ProgressIndicator,
  tags: ['autodocs'],
  parameters: { layout: 'padded' },
} satisfies Meta<typeof ProgressIndicator>

export default meta
type Story = StoryObj<typeof meta>

export const Determinate: Story = {
  args: {
    variant: 'determinate',
    value: 42,
    label: 'Training model',
    description: 'Estimated 3 minutes remaining',
  },
}

export const DeterminateZero: Story = {
  args: { variant: 'determinate', value: 0, label: 'Starting' },
}

export const DeterminateDone: Story = {
  args: { variant: 'determinate', value: 100, label: 'Complete' },
}

export const Indeterminate: Story = {
  args: {
    variant: 'indeterminate',
    label: 'Preparing dataset',
    description: 'This may take a few moments...',
  },
}

export const IndeterminateWarning: Story = {
  args: {
    variant: 'indeterminate',
    label: 'Provisioning environment',
    description: 'Taking longer than expected',
    startedAt: new Date(Date.now() - 5 * 60 * 1000),
    warningAfterSeconds: 60,
  },
}

export const Stages: Story = {
  args: {
    variant: 'stages',
    label: 'Fine-tuning pipeline',
    stages: [
      { id: 'q', label: 'Queued', status: 'done', description: 'Submitted 2 minutes ago' },
      { id: 'p', label: 'Preparing dataset', status: 'done', description: '1,245 samples' },
      { id: 't', label: 'Training', status: 'running', description: 'Epoch 3 of 10 -- 34%' },
      { id: 'e', label: 'Evaluating', status: 'pending' },
      { id: 'd', label: 'Deploying', status: 'pending' },
    ],
  },
}

export const StagesAllDone: Story = {
  args: {
    variant: 'stages',
    label: 'Setup wizard',
    stages: [
      { id: 'c', label: 'Company configured', status: 'done' },
      { id: 'p', label: 'Providers added', status: 'done' },
      { id: 'a', label: 'Agents personalized', status: 'done' },
      { id: 't', label: 'Theme selected', status: 'done' },
    ],
  },
}

export const StagesWithFailure: Story = {
  args: {
    variant: 'stages',
    label: 'Provider connection',
    stages: [
      { id: 'r', label: 'Resolve endpoint', status: 'done' },
      { id: 'a', label: 'Authenticate', status: 'done' },
      { id: 'p', label: 'Probe capabilities', status: 'failed', description: 'HTTP 503 -- retrying in 15s' },
      { id: 'd', label: 'Discover models', status: 'pending' },
    ],
  },
}

export const StagesEmpty: Story = {
  args: {
    variant: 'stages',
    label: 'No stages yet',
    description: 'Pipeline has not started.',
    stages: [],
  },
}
