import type { Meta, StoryObj } from '@storybook/react-vite'
import { mockTunnelProviders, tunnelHandlers } from '@/mocks/handlers/tunnel'
import { useTunnelStore } from '@/stores/tunnel'
import { TunnelCard } from './TunnelCard'

const meta = {
  title: 'Pages/Connections/TunnelCard',
  component: TunnelCard,
  tags: ['autodocs'],
  parameters: {
    msw: { handlers: tunnelHandlers },
  },
  decorators: [
    (Story) => (
      <div className="max-w-2xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof TunnelCard>

export default meta
type Story = StoryObj<typeof meta>

const baseProviders = {
  providers: mockTunnelProviders,
  selectedProvider: 'cloudflare',
}

export const Stopped: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.getState().reset()
      useTunnelStore.setState({ ...baseProviders })
      return <Story />
    },
  ],
}

export const NgrokTokenNeeded: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.getState().reset()
      useTunnelStore.setState({ ...baseProviders, selectedProvider: 'ngrok' })
      return <Story />
    },
  ],
}

export const DevTunnelsUnavailable: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.getState().reset()
      useTunnelStore.setState({ ...baseProviders, selectedProvider: 'devtunnels' })
      return <Story />
    },
  ],
}

export const DeviceLoginPrompt: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.getState().reset()
      useTunnelStore.setState({
        providers: mockTunnelProviders.map((p) =>
          p.provider_id === 'devtunnels' ? { ...p, available: true, detail: null } : p,
        ),
        selectedProvider: 'devtunnels',
        deviceLogin: {
          verification_uri: 'https://github.com/login/device',
          user_code: 'ABCD-1234',
          already_logged_in: false,
        },
      })
      return <Story />
    },
  ],
}

export const Enabling: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.setState({
        ...baseProviders,
        phase: 'enabling',
        publicUrl: null,
        error: null,
      })
      return <Story />
    },
  ],
}

export const Running: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.setState({
        ...baseProviders,
        phase: 'on',
        publicUrl: 'https://mock-tunnel.trycloudflare.com',
        activeProvider: 'cloudflare',
        error: null,
      })
      return <Story />
    },
  ],
}

export const Error: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.setState({
        ...baseProviders,
        phase: 'error',
        publicUrl: null,
        error: 'cloudflared produced no quick-tunnel URL within 60s',
      })
      return <Story />
    },
  ],
}
