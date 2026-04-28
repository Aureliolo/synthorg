import type { Meta, StoryObj } from '@storybook/react-vite'
import { RateLimitsDrawer } from './RateLimitsDrawer'
import { useProvidersStore } from '@/stores/providers'

const meta = {
  title: 'Providers/RateLimitsDrawer',
  component: RateLimitsDrawer,
  args: {
    providerName: 'test-provider',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        rateLimits: { requests_per_minute: 60, concurrent_requests: 10 },
        rateLimitsLoading: false,
        rateLimitsError: null,
        fetchRateLimits: async () => {},
        updateRateLimits: async () => ({
          requests_per_minute: 60,
          concurrent_requests: 10,
        }),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof RateLimitsDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Unlimited: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        rateLimits: { requests_per_minute: 0, concurrent_requests: 0 },
      })
      return <Story />
    },
  ],
}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        rateLimits: null,
        rateLimitsLoading: true,
      })
      return <Story />
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        rateLimits: null,
        rateLimitsLoading: false,
        rateLimitsError: 'Failed to load: backend unreachable',
      })
      return <Story />
    },
  ],
}

export const Closed: Story = {
  args: { open: false },
}
