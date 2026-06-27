import type { Meta, StoryObj } from '@storybook/react-vite'
import { tunnelHandlers } from '@/mocks/handlers/tunnel'
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

export const Stopped: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.getState().reset()
      return <Story />
    },
  ],
}

export const Enabling: Story = {
  decorators: [
    (Story) => {
      useTunnelStore.setState({
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
        phase: 'on',
        publicUrl: 'https://mock-tunnel.ngrok.io',
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
        phase: 'error',
        publicUrl: null,
        error: 'Failed to authenticate with ngrok',
      })
      return <Story />
    },
  ],
}
