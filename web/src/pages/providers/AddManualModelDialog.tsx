import { Dialog } from '@base-ui/react/dialog'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { useProvidersStore } from '@/stores/providers'
import { useSettingsStore } from '@/stores/settings'
import type { ProviderModelConfig } from '@/api/types/providers'

interface AddManualModelDialogProps {
  providerName: string | null
  open: boolean
  onClose: () => void
}

function parsePositiveInt(raw: string): number | null {
  const t = raw.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) && Number.isInteger(n) && n > 0 ? n : null
}

function parseNonNegFloat(raw: string): number | null {
  const t = raw.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) && n >= 0 ? n : null
}

/**
 * Modal for the manual model add flow.  Bypasses discovery; the
 * operator types in the model id and pricing.  Conflict (model id
 * already exists) becomes an error toast surfaced by the store.
 */
export function AddManualModelDialog({
  providerName,
  open,
  onClose,
}: AddManualModelDialogProps) {
  const addProviderModel = useProvidersStore((s) => s.addProviderModel)
  const currency = useSettingsStore((s) => s.currency)
  // Track open-state in a ref so a slow add-model request that
  // resolves after the dialog closes does not wipe new-session
  // inputs by triggering ``reset()`` + ``onClose()`` on the new
  // form instance (mirrors SyncModelsConfirmDialog).
  const openRef = useRef(open)
  useEffect(() => {
    openRef.current = open
  }, [open])

  const [modelId, setModelId] = useState('')
  const [alias, setAlias] = useState('')
  const [costInput, setCostInput] = useState('')
  const [costOutput, setCostOutput] = useState('')
  const [maxContext, setMaxContext] = useState('')
  const [latencyMs, setLatencyMs] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  const reset = (): void => {
    setModelId('')
    setAlias('')
    setCostInput('')
    setCostOutput('')
    setMaxContext('')
    setLatencyMs('')
    setSubmitting(false)
    setValidationError(null)
  }

  const handleSubmit = async (): Promise<void> => {
    if (!providerName) return
    const idTrimmed = modelId.trim()
    if (idTrimmed === '') {
      setValidationError('Model id is required.')
      return
    }
    const ctxValue = parsePositiveInt(maxContext)
    if (maxContext.trim() !== '' && ctxValue === null) {
      setValidationError('Max context must be a positive integer.')
      return
    }
    const inputCost = parseNonNegFloat(costInput)
    if (costInput.trim() !== '' && inputCost === null) {
      setValidationError('Input cost must be a non-negative number.')
      return
    }
    const outputCost = parseNonNegFloat(costOutput)
    if (costOutput.trim() !== '' && outputCost === null) {
      setValidationError('Output cost must be a non-negative number.')
      return
    }
    const latency = parsePositiveInt(latencyMs)
    if (latencyMs.trim() !== '' && latency === null) {
      setValidationError('Latency must be a positive integer (milliseconds).')
      return
    }
    const model: ProviderModelConfig = {
      id: idTrimmed,
      alias: alias.trim() || null,
      cost_per_1k_input: inputCost ?? 0,
      cost_per_1k_output: outputCost ?? 0,
      max_context: ctxValue ?? 200_000,
      estimated_latency_ms: latency ?? null,
      local_params: null,
    }
    setValidationError(null)
    setSubmitting(true)
    const result = await addProviderModel(providerName, { model })
    if (!openRef.current) return
    setSubmitting(false)
    if (result !== null) {
      reset()
      onClose()
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset()
          onClose()
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 bg-overlay backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-popup w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card p-card-tight sm:p-card md:p-card-roomy shadow-card-hover">
          <Dialog.Title className="text-lg font-semibold text-foreground">
            Add model manually
          </Dialog.Title>
          <Dialog.Description className="text-sm text-text-secondary">
            Use this when the model is not in the LiteLLM catalog and
            discovery does not surface it.  Pricing fields are
            optional; leave them blank for free or unknown.
          </Dialog.Description>

          {validationError && (
            <div className="mt-section-gap">
              <ErrorBanner severity="warning" title={validationError} />
            </div>
          )}

          <div className="mt-section-gap flex flex-col gap-grid-gap">
            <InputField
              label="Model id"
              hint="The exact identifier the provider's API expects"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              required
            />
            <InputField
              label="Alias"
              hint="Optional shorthand for routing rules"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
            />
            <div className="grid grid-cols-2 gap-grid-gap">
              <InputField
                label="Cost / 1k input tokens"
                hint={`${currency}; leave blank for 0`}
                type="number"
                inputMode="decimal"
                value={costInput}
                onChange={(e) => setCostInput(e.target.value)}
                min={0}
                step="0.0001"
              />
              <InputField
                label="Cost / 1k output tokens"
                hint={`${currency}; leave blank for 0`}
                type="number"
                inputMode="decimal"
                value={costOutput}
                onChange={(e) => setCostOutput(e.target.value)}
                min={0}
                step="0.0001"
              />
            </div>
            <div className="grid grid-cols-2 gap-grid-gap">
              <InputField
                label="Max context (tokens)"
                hint="Defaults to 200k"
                type="number"
                inputMode="numeric"
                value={maxContext}
                onChange={(e) => setMaxContext(e.target.value)}
                min={1}
              />
              <InputField
                label="Latency (ms)"
                hint="Optional; used by fastest routing"
                type="number"
                inputMode="numeric"
                value={latencyMs}
                onChange={(e) => setLatencyMs(e.target.value)}
                min={1}
              />
            </div>
          </div>

          <div className="mt-section-gap flex justify-end gap-grid-gap">
            <Button
              variant="secondary"
              onClick={() => {
                reset()
                onClose()
              }}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button onClick={() => void handleSubmit()} disabled={submitting}>
              {submitting ? 'Adding…' : 'Add model'}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
