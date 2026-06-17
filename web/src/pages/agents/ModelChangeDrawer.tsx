import { useEffect, useMemo, useRef, useState } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { Button } from '@/components/ui/button'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import { useAgentsStore } from '@/stores/agents'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderConfig } from '@/api/types/providers'

export interface ModelChangeDrawerProps {
  agentId: string
  /** Current provider name, from the agent's raw model config. */
  currentProvider: string
  /** Current model id, from the agent's raw model config. */
  currentModelId: string
  open: boolean
  onClose: () => void
}

interface ModelChangeFormProps {
  agentId: string
  currentProvider: string
  currentModelId: string
  providerMap: Record<string, ProviderConfig>
  onClose: () => void
}

/**
 * The selection form. Mounted with a ``key`` derived from the agent +
 * current model so it initialises its local state from props on every
 * open without a prop-to-state sync effect.
 */
function ModelChangeForm({
  agentId,
  currentProvider,
  currentModelId,
  providerMap,
  onClose,
}: ModelChangeFormProps) {
  const updateAgentModel = useAgentsStore((s) => s.updateAgentModel)
  const updatingModel = useAgentsStore((s) => s.updatingModel)
  const [provider, setProvider] = useState(currentProvider)
  const [modelId, setModelId] = useState(currentModelId)

  const unchanged = provider === currentProvider && modelId === currentModelId
  const handleSave = async () => {
    const ok = await updateAgentModel(agentId, provider, modelId)
    if (ok) onClose()
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <p className="text-sm text-muted-foreground">
        Re-point this agent at a different provider and model. The pair is
        validated against the live catalogue before it is applied.
      </p>
      <AgentModelPicker
        currentProvider={provider}
        currentModelId={modelId}
        providers={providerMap}
        onChange={(p, m) => {
          setProvider(p)
          setModelId(m)
        }}
        disabled={updatingModel}
      />
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={updatingModel}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} disabled={updatingModel || unchanged}>
          {updatingModel ? 'Saving...' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

/**
 * Post-setup agent model change. Re-uses the shared family/capability
 * model picker over the live provider catalogue; the save path runs the
 * catalogue-validated ``PATCH /agents/{id}`` mutation via the store.
 */
export function ModelChangeDrawer({
  agentId,
  currentProvider,
  currentModelId,
  open,
  onClose,
}: ModelChangeDrawerProps) {
  const providers = useProvidersStore((s) => s.providers)
  const fetchProviders = useProvidersStore((s) => s.fetchProviders)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (open && !fetchedRef.current && providers.length === 0) {
      fetchedRef.current = true
      void fetchProviders()
    }
  }, [open, providers.length, fetchProviders])

  const providerMap = useMemo<Record<string, ProviderConfig>>(
    () => Object.fromEntries(providers.map((p) => [p.name, p])),
    [providers],
  )

  return (
    <Drawer open={open} onClose={onClose} title="Change Model">
      <ModelChangeForm
        key={`${agentId}:${currentProvider}:${currentModelId}`}
        agentId={agentId}
        currentProvider={currentProvider}
        currentModelId={currentModelId}
        providerMap={providerMap}
        onClose={onClose}
      />
    </Drawer>
  )
}
