import { useEffect } from 'react'
import { useTunnelStore } from '@/stores/tunnel'
import type { TunnelPhase } from '@/stores/tunnel'

export interface UseTunnelDataReturn {
  phase: TunnelPhase
  publicUrl: string | null
  error: string | null
  autoStop: boolean
  hasAuthToken: boolean | null
}

export function useTunnelData(): UseTunnelDataReturn {
  const phase = useTunnelStore((s) => s.phase)
  const publicUrl = useTunnelStore((s) => s.publicUrl)
  const error = useTunnelStore((s) => s.error)
  const autoStop = useTunnelStore((s) => s.autoStop)
  const hasAuthToken = useTunnelStore((s) => s.hasAuthToken)

  // Fetch once on mount; phase transitions are driven by user actions.
  useEffect(() => {
    void useTunnelStore.getState().fetchStatus()
  }, [])

  return { phase, publicUrl, error, autoStop, hasAuthToken }
}
