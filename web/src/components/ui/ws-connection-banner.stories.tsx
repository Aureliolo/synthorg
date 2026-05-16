import type { Meta, StoryObj } from '@storybook/react'
import { useEffect } from 'react'
import { WsConnectionBanner } from './ws-connection-banner'
import { useWebSocketStore } from '@/stores/websocket'

const meta: Meta<typeof WsConnectionBanner> = {
  title: 'Feedback/WsConnectionBanner',
  component: WsConnectionBanner,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
    a11y: { test: 'error' },
  },
  // The banner reads from the websocket store; force the connection
  // state for the duration of each story so the story renders
  // predictably regardless of real network state in Storybook.
  decorators: [
    (Story, { parameters }) => {
      const connected = (parameters as { wsConnected?: boolean }).wsConnected ?? false
      useEffect(() => {
        const previous = useWebSocketStore.getState().connected
        useWebSocketStore.setState({ connected })
        return () => {
          useWebSocketStore.setState({ connected: previous })
        }
      }, [connected])
      return <Story />
    },
  ],
}

export default meta
type Story = StoryObj<typeof WsConnectionBanner>

export const Offline: Story = {
  parameters: { wsConnected: false },
}

export const OfflineCustomCopy: Story = {
  args: {
    title: 'Connections updates paused',
    description: 'Connection state may be stale until the link recovers.',
  },
  parameters: { wsConnected: false },
}

/** When the socket is connected, the banner renders nothing. */
export const Connected: Story = {
  parameters: { wsConnected: true },
}
