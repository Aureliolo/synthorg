import { useEffect } from 'react'
import { useTunnelStore } from '@/stores/tunnel'
import type { TunnelPhase } from '@/stores/tunnel'
import type {
  DeviceLoginPrompt,
  TunnelProviderStatus,
} from '@/api/types/integrations'

export interface UseTunnelDataReturn {
  phase: TunnelPhase
  publicUrl: string | null
  error: string | null
  autoStop: boolean
  providers: readonly TunnelProviderStatus[]
  selectedProvider: string | null
  activeProvider: string | null
  deviceLogin: DeviceLoginPrompt | null
  connectingDevice: string | null
}

export function useTunnelData(): UseTunnelDataReturn {
  const phase = useTunnelStore((s) => s.phase)
  const publicUrl = useTunnelStore((s) => s.publicUrl)
  const error = useTunnelStore((s) => s.error)
  const autoStop = useTunnelStore((s) => s.autoStop)
  const providers = useTunnelStore((s) => s.providers)
  const selectedProvider = useTunnelStore((s) => s.selectedProvider)
  const activeProvider = useTunnelStore((s) => s.activeProvider)
  const deviceLogin = useTunnelStore((s) => s.deviceLogin)
  const connectingDevice = useTunnelStore((s) => s.connectingDevice)

  // Fetch once on mount; phase transitions are driven by user actions.
  useEffect(() => {
    void useTunnelStore.getState().fetchStatus()
  }, [])

  return {
    phase,
    publicUrl,
    error,
    autoStop,
    providers,
    selectedProvider,
    activeProvider,
    deviceLogin,
    connectingDevice,
  }
}
