import type { Meta, StoryObj } from '@storybook/react'
import { ErrorBanner } from './error-banner'

const meta = {
  title: 'Feedback/ErrorBanner',
  component: ErrorBanner,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof ErrorBanner>

export default meta
type Story = StoryObj<typeof meta>

export const Error: Story = {
  args: {
    variant: 'section',
    severity: 'error',
    title: 'Failed to load tasks',
    description: 'The server returned an error. Check your connection and try again.',
    onRetry: () => {},
  },
}

export const Warning: Story = {
  args: {
    variant: 'section',
    severity: 'warning',
    title: 'Some providers are degraded',
    description: '2 of 5 configured providers are reporting elevated latency.',
  },
}

export const Info: Story = {
  args: {
    variant: 'section',
    severity: 'info',
    title: 'A new version is available',
    description: 'Reload the page to get the latest dashboard.',
    action: { label: 'Reload', onClick: () => {} },
  },
}

export const Inline: Story = {
  args: {
    variant: 'inline',
    severity: 'error',
    title: 'Failed to update model',
    onRetry: () => {},
  },
}

export const Offline: Story = {
  args: {
    variant: 'offline',
    title: 'You are offline',
    description: 'Changes will sync when the connection is restored.',
    onDismiss: () => {},
  },
}

export const WithDismiss: Story = {
  args: {
    variant: 'section',
    severity: 'warning',
    title: 'Unsaved draft available',
    description: 'We restored your unsaved changes from 5 minutes ago.',
    onRetry: () => {},
    onDismiss: () => {},
  },
}

export const LongDescription: Story = {
  args: {
    variant: 'section',
    severity: 'error',
    title: 'Provider probe failed',
    description:
      'Unable to reach the provider at https://api.example.com/v1. The host returned HTTP 503 (Service Unavailable) after 3 retries over 15 seconds. This is usually transient. If the error persists, check the provider configuration and verify network reachability from the dashboard host.',
    onRetry: () => {},
  },
}

export const AllControls: Story = {
  args: {
    variant: 'section',
    severity: 'error',
    title: 'Activation failed',
    description: 'The workflow could not be activated. Retry, dismiss, or open the run log.',
    onRetry: () => {},
    onDismiss: () => {},
    action: { label: 'View log', onClick: () => {} },
  },
}

export const TitleOnlyError: Story = {
  args: {
    variant: 'inline',
    severity: 'error',
    title: 'Failed to delete agent',
  },
}

export const AllSeverities: Story = {
  render: () => (
    <div className="flex flex-col gap-3">
      <ErrorBanner severity="error" title="Error banner" description="Error-level severity uses role=alert." onRetry={() => {}} />
      <ErrorBanner severity="warning" title="Warning banner" description="Warning-level severity uses role=status polite." />
      <ErrorBanner severity="info" title="Info banner" description="Info-level severity uses role=status polite." />
      <ErrorBanner variant="offline" title="Offline banner" description="Offline variant forces warning severity and WifiOff icon." />
      <ErrorBanner variant="inline" severity="error" title="Inline error" onRetry={() => {}} />
    </div>
  ),
  args: { title: '' },
}
