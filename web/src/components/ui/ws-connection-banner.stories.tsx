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
      const p = parameters as {
        wsConnected?: boolean
        sseFallbackActive?: boolean
        protocolVersionMismatch?: boolean
      }
      const connected = p.wsConnected ?? false
      const sseFallbackActive = p.sseFallbackActive ?? false
      const protocolVersionMismatch = p.protocolVersionMismatch ?? false
      useEffect(() => {
        const s = useWebSocketStore.getState()
        const previous = {
          connected: s.connected,
          sseFallbackActive: s.sseFallbackActive,
          sseFallbackExhausted: s.sseFallbackExhausted,
          protocolVersionMismatch: s.protocolVersionMismatch,
        }
        useWebSocketStore.setState({
          connected,
          sseFallbackActive,
          sseFallbackExhausted: false,
          protocolVersionMismatch,
        })
        return () => {
          useWebSocketStore.setState(previous)
        }
      }, [connected, sseFallbackActive, protocolVersionMismatch])
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

/**
 * WebSocket is blocked (e.g. a proxy that drops the upgrade) and events
 * are arriving over the read-only SSE fallback: a degraded-but-live
 * warning rather than a hard offline state.
 */
export const Degraded: Story = {
  parameters: { wsConnected: false, sseFallbackActive: true },
}

/**
 * The server advanced its wire protocol past the client: the socket
 * still looks connected but events no longer decode, so the banner
 * prompts a reload even while `connected` is true.
 */
export const ProtocolMismatch: Story = {
  parameters: { wsConnected: true, protocolVersionMismatch: true },
}
