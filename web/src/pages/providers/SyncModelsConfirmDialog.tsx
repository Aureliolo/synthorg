import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ToggleField } from '@/components/ui/toggle-field'
import { useProvidersStore } from '@/stores/providers'
import type { SyncModelsResponse } from '@/api/types/providers'

interface SyncModelsConfirmDialogProps {
  providerName: string | null
  presetHint?: string
  open: boolean
  onClose: () => void
}

/**
 * Confirmation modal for the bulk model sync flow.  Defaults to
 * ``replace_existing=true``; the operator can opt into append-only
 * merge via a toggle.  After a successful sync, the result banner
 * shows the diff (added / removed / updated) until dismissed.
 */
export function SyncModelsConfirmDialog({
  providerName,
  presetHint,
  open,
  onClose,
}: SyncModelsConfirmDialogProps) {
  const syncProviderModels = useProvidersStore((s) => s.syncProviderModels)

  // Default to the non-destructive append-only mode. The destructive
  // ``replace_existing=true`` path can delete every persisted model
  // if discovery returns an empty set (wrong URL / network blip /
  // provider outage); the backend now refuses that case explicitly,
  // but the safe default for the click path is still merge-not-replace
  // so a single mis-clicked button cannot lose data.
  const [replaceExisting, setReplaceExisting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<SyncModelsResponse | null>(null)

  // Track open-state in a ref so an in-flight sync that resolves
  // after the dialog closes does not repopulate ``result`` /
  // ``submitting`` and visually re-open with a stale "Sync complete"
  // banner.  ``open`` is the canonical truth; the ref mirrors it
  // without forcing a re-render on close.
  const openRef = useRef(open)
  useEffect(() => {
    openRef.current = open
  }, [open])

  const reset = (): void => {
    setReplaceExisting(false)
    setSubmitting(false)
    setResult(null)
  }

  const handleConfirm = async (): Promise<void> => {
    if (!providerName) return
    setSubmitting(true)
    const response = await syncProviderModels(providerName, {
      replace_existing: replaceExisting,
      ...(presetHint ? { preset_hint: presetHint } : {}),
    })
    if (!openRef.current) return
    setSubmitting(false)
    if (response !== null) {
      setResult(response)
    }
  }

  const summary =
    result &&
    `${result.added.length} added · ${result.removed.length} removed · ${result.updated.length} updated`

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset()
          onClose()
        }
      }}
      title="Sync models from provider"
      description={
        replaceExisting
          ? 'Re-runs discovery and replaces the persisted model list with the merged result.'
          : 'Re-runs discovery and appends only newly-discovered models. Existing models keep their persisted config.'
      }
      variant={replaceExisting ? 'destructive' : 'default'}
      confirmLabel={submitting ? 'Syncing…' : 'Sync now'}
      onConfirm={handleConfirm}
      loading={submitting}
    >
      <div className="flex flex-col gap-grid-gap">
        <ToggleField
          label="Replace existing models"
          description="Off = append-only merge (keeps persisted config on existing models)"
          checked={replaceExisting}
          onChange={setReplaceExisting}
          disabled={submitting || result !== null}
        />
        {result && summary && (
          <ErrorBanner
            severity="info"
            title="Sync complete"
            description={summary}
            action={
              <Button
                variant="ghost"
                onClick={() => {
                  reset()
                  onClose()
                }}
              >
                Close
              </Button>
            }
          />
        )}
      </div>
    </ConfirmDialog>
  )
}
