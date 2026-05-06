import { useCallback, useEffect, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, PackagePlus, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { listTemplatePacks, applyTemplatePack } from '@/api/endpoints/template-packs'
import { useToastStore } from '@/stores/toast'
import { useCompanyStore } from '@/stores/company'
import { getErrorMessage } from '@/utils/errors'
import type { PackInfoResponse, RebalanceMode } from '@/api/types/templates'
import { PackApplyPreviewDialog } from './PackApplyPreviewDialog'

const EMPTY_DEPTS: readonly import('@/api/types/org').Department[] = []

export interface PackSelectionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  disabled?: boolean
}

interface PackListItemProps {
  pack: PackInfoResponse
  disabled: boolean
  busy: boolean
  applying: string | null
  onApply: (name: string) => void
}

function PackListItem({ pack, disabled, busy, applying, onApply }: PackListItemProps) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      onClick={() => onApply(pack.name)}
      className={cn(
        'w-full rounded-lg border border-border p-card text-left transition-colors',
        'hover:border-accent hover:bg-card/50',
        'disabled:opacity-50 disabled:cursor-not-allowed',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground truncate">
            {pack.display_name}
          </p>
          <p className="text-xs text-text-secondary mt-0.5 line-clamp-2">
            {pack.description}
          </p>
        </div>
        {applying === pack.name && (
          <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
        )}
      </div>
      <div className="flex items-center gap-2 mt-2">
        <StatPill label="Agents" value={pack.agent_count} />
        <StatPill label="Depts" value={pack.department_count} />
        {pack.source === 'user' && (
          <span className="text-[10px] text-text-secondary bg-card rounded px-1.5 py-0.5">
            custom
          </span>
        )}
      </div>
    </button>
  )
}

export function PackSelectionDialog({ open, onOpenChange, disabled }: PackSelectionDialogProps) {
  const [packs, setPacks] = useState<readonly PackInfoResponse[]>([])
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPack, setSelectedPack] = useState<PackInfoResponse | null>(null)
  const addToast = useToastStore((s) => s.add)
  const currentDepts = useCompanyStore((s) => s.config?.departments) ?? EMPTY_DEPTS

  // Track open transitions via ref so the effect only fires on changes.
  const prevOpenRef = useRef(open)
  if (open && !prevOpenRef.current) {
    setLoading(true)
    setError(null)
    setApplying(null)
  }
  if (!open && prevOpenRef.current) {
    setError(null)
    setApplying(null)
    setLoading(false)
  }
  prevOpenRef.current = open

  useEffect(() => {
    if (!open) return
    let cancelled = false
    listTemplatePacks()
      .then((data) => {
        if (!cancelled) setPacks(data)
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const handleApplyDirect = useCallback(
    async (packName: string) => {
      setApplying(packName)
      setError(null)
      try {
        const result = await applyTemplatePack({ pack_name: packName })
        addToast({
          variant: 'success',
          title: `Added ${result.agents_added} agent(s) and ${result.departments_added} department(s)`,
        })
        onOpenChange(false)
        try {
          await useCompanyStore.getState().fetchCompanyData()
        } catch {
          addToast({
            variant: 'warning',
            title: 'Pack applied but failed to refresh data. Reload the page.',
          })
        }
      } catch (err) {
        setError(getErrorMessage(err))
      } finally {
        setApplying(null)
      }
    },
    [addToast, onOpenChange],
  )

  const handleSelectPack = useCallback((pack: PackInfoResponse) => {
    if (pack.department_count > 0) {
      // Pack adds departments -- show budget preview before applying.
      setSelectedPack(pack)
    } else {
      // No departments -- apply directly (no budget impact).
      void handleApplyDirect(pack.name)
    }
  }, [handleApplyDirect])

  const handleApplyWithMode = useCallback(
    async (packName: string, rebalanceMode: RebalanceMode) => {
      setApplying(packName)
      setError(null)
      try {
        const result = await applyTemplatePack({
          pack_name: packName,
          rebalance_mode: rebalanceMode,
        })
        let title = `Added ${result.agents_added} agent(s) and ${result.departments_added} department(s).`
        title += ` Budget: ${result.budget_before.toFixed(1)}% -> ${result.budget_after.toFixed(1)}%`
        if (result.scale_factor !== null && result.scale_factor < 1) {
          title += ` (existing scaled to ${(result.scale_factor * 100).toFixed(0)}%)`
        }
        addToast({ variant: 'success', title })
        setSelectedPack(null)
        onOpenChange(false)
        try {
          await useCompanyStore.getState().fetchCompanyData()
        } catch {
          addToast({
            variant: 'warning',
            title: 'Pack applied but failed to refresh data. Reload the page.',
          })
        }
      } catch (err) {
        setError(getErrorMessage(err))
      } finally {
        setApplying(null)
      }
    },
    [addToast, onOpenChange],
  )

  const busy = applying !== null

  return (
    <>
    <Dialog.Root open={open} onOpenChange={(v: boolean) => { if (!busy) onOpenChange(v) }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-200 ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-base font-semibold text-foreground flex items-center gap-2">
              <PackagePlus className="size-4 text-accent" />
              Add Team
            </Dialog.Title>
            <Dialog.Close
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Close"
                  disabled={busy}
                >
                  <X className="size-4" />
                </Button>
              }
            />
          </div>

          <Dialog.Description className="text-sm text-text-secondary mb-4">
            Select a pre-configured team pack to add to your organization.
          </Dialog.Description>

          {loading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="size-6 animate-spin text-text-secondary" />
            </div>
          )}

          {!loading && error && (
            <p className="text-xs text-danger py-4">{error}</p>
          )}

          {!loading && !error && packs.length === 0 && (
            <EmptyState
              icon={PackagePlus}
              title="No packs available"
              description="No template packs are available at this time."
            />
          )}

          {!loading && !error && packs.length > 0 && (
            <StaggerGroup className="space-y-2 max-h-80 overflow-y-auto pr-1">
              {packs.map((pack) => (
                <StaggerItem key={pack.name}>
                  <PackListItem
                    pack={pack}
                    disabled={disabled ?? false}
                    busy={busy}
                    applying={applying}
                    onApply={() => handleSelectPack(pack)}
                  />
                </StaggerItem>
              ))}
            </StaggerGroup>
          )}

          <div className="flex justify-end pt-4">
            <Dialog.Close
              render={
                <Button variant="outline" disabled={busy}>Close</Button>
              }
            />
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>

      <PackApplyPreviewDialog
        open={selectedPack !== null}
        onOpenChange={(isOpen) => { if (!isOpen) setSelectedPack(null) }}
        pack={selectedPack}
        currentDepartments={currentDepts}
        onApply={handleApplyWithMode}
        applying={applying !== null}
      />
    </>
  )
}
