import { useCallback, useEffect, useRef, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { useCollaborationStore } from '@/stores/collaboration'
import type { OverrideResponse } from '@/api/types/collaboration'

const OVERRIDE_ROLES = ['ceo', 'manager'] as const

export interface CollaborationOverrideController {
  override: OverrideResponse | null
  loading: boolean
  loadError: boolean
  canManageOverrides: boolean
  clearDialogOpen: boolean
  clearing: boolean
  setClearDialogOpen: (open: boolean) => void
  fetchOverride: () => Promise<void>
  handleClear: () => Promise<boolean>
}

export function useCollaborationOverride(
  agentId: string,
): CollaborationOverrideController {
  const [override, setOverride] = useState<OverrideResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const { userRole } = useAuth()
  const canManageOverrides =
    userRole !== null && (OVERRIDE_ROLES as readonly string[]).includes(userRole)

  // Guard against a slow response for a previous agent landing after the
  // panel switched agents.
  const activeAgentRef = useRef(agentId)
  activeAgentRef.current = agentId

  // React's "reset state when props change" idiom: clear transient flags
  // during the render that observes the new agent.
  const prevAgentIdRef = useRef(agentId)
  if (prevAgentIdRef.current !== agentId) {
    prevAgentIdRef.current = agentId
    setClearing(false)
    setClearDialogOpen(false)
  }

  const fetchOverride = useCallback(async () => {
    setLoading(true)
    setOverride(null)
    setLoadError(false)
    try {
      const result = await useCollaborationStore.getState().getOverride(agentId)
      if (activeAgentRef.current !== agentId) return
      if (result.kind === 'ok') setOverride(result.data)
      else if (result.kind === 'error') setLoadError(true)
    } finally {
      // Only the latest request may clear loading: a slow response for a
      // previous agent must not unset loading while the current fetch runs.
      if (activeAgentRef.current === agentId) {
        setLoading(false)
      }
    }
  }, [agentId])

  useEffect(() => {
    void fetchOverride()
  }, [fetchOverride])

  const handleClear = useCallback(async () => {
    setClearing(true)
    const requestAgent = agentId
    const ok = await useCollaborationStore.getState().clearOverride(requestAgent)
    if (activeAgentRef.current !== requestAgent) {
      setClearing(false)
      return false
    }
    setClearing(false)
    if (!ok) return false
    setOverride(null)
    setClearDialogOpen(false)
    return true
  }, [agentId])

  return {
    override,
    loading,
    loadError,
    canManageOverrides,
    clearDialogOpen,
    clearing,
    setClearDialogOpen,
    fetchOverride,
    handleClear,
  }
}
