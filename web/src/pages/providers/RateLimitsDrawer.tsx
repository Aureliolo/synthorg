import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useProvidersStore } from '@/stores/providers'
import type { RateLimitsConfig } from '@/api/types/providers'

interface RateLimitsDrawerProps {
  providerName: string | null
  open: boolean
  onClose: () => void
}

function parseCap(value: string): number | null {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < 0) {
    return null
  }
  return parsed
}

function formatCap(value: number): string {
  return value === 0 ? '' : String(value)
}

/**
 * Inner form component.  Initial state is seeded from the read
 * ``rateLimits`` prop on mount; if the read state changes, the
 * outer drawer re-mounts via ``key={...}`` instead of patching
 * state via an effect.  This avoids the ``set-state-in-effect``
 * anti-pattern.
 */
function RateLimitsForm({
  providerName,
  initial,
  onClose,
}: {
  providerName: string
  initial: RateLimitsConfig
  onClose: () => void
}) {
  const updateRateLimits = useProvidersStore((s) => s.updateRateLimits)
  const [rpm, setRpm] = useState(() => formatCap(initial.requests_per_minute))
  const [concurrent, setConcurrent] = useState(() =>
    formatCap(initial.concurrent_requests),
  )
  const [saving, setSaving] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleSave = async (): Promise<void> => {
    const rpmCap = parseCap(rpm)
    const concurrentCap = parseCap(concurrent)
    if (rpmCap === null && rpm.trim() !== '') {
      setValidationError('Requests per minute must be a non-negative integer.')
      return
    }
    if (concurrentCap === null && concurrent.trim() !== '') {
      setValidationError('Concurrent requests must be a non-negative integer.')
      return
    }
    const updates: { requests_per_minute?: number; concurrent_requests?: number } = {}
    if (rpmCap !== initial.requests_per_minute) {
      updates.requests_per_minute = rpmCap ?? 0
    }
    if (concurrentCap !== initial.concurrent_requests) {
      updates.concurrent_requests = concurrentCap ?? 0
    }
    if (Object.keys(updates).length === 0) {
      setValidationError('No fields changed.')
      return
    }
    setValidationError(null)
    setSaving(true)
    const result = await updateRateLimits(providerName, updates)
    setSaving(false)
    if (result !== null) {
      onClose()
    }
  }

  return (
    <>
      {validationError && (
        <ErrorBanner severity="warning" title={validationError} />
      )}
      <InputField
        label="Requests per minute"
        value={rpm}
        onChange={(e) => setRpm(e.target.value)}
        hint="Empty or 0 = unlimited"
        type="number"
        inputMode="numeric"
        min={0}
      />
      <InputField
        label="Max concurrent requests"
        value={concurrent}
        onChange={(e) => setConcurrent(e.target.value)}
        hint="Empty or 0 = unlimited"
        type="number"
        inputMode="numeric"
        min={0}
      />
      <div className="flex justify-end gap-grid-gap pt-card">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={() => void handleSave()} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </>
  )
}

/**
 * Drawer for reading + partially updating one provider's rate-limit
 * configuration.  ``0`` (an empty input) means "unlimited" on both
 * fields, matching the persisted ``RateLimiterConfig`` semantics.
 *
 * Pure form component: relies on the providers store for both the
 * read state (``rateLimits``) and the mutation (``updateRateLimits``)
 * per the canonical store-owned-error-UX contract; no try/catch
 * here.
 */
export function RateLimitsDrawer({
  providerName,
  open,
  onClose,
}: RateLimitsDrawerProps) {
  const rateLimits = useProvidersStore((s) => s.rateLimits)
  const loading = useProvidersStore((s) => s.rateLimitsLoading)
  const error = useProvidersStore((s) => s.rateLimitsError)
  const rateLimitsProviderName = useProvidersStore(
    (s) => s.rateLimitsProviderName,
  )
  const fetchRateLimits = useProvidersStore((s) => s.fetchRateLimits)

  useEffect(() => {
    if (open && providerName) {
      void fetchRateLimits(providerName)
    }
  }, [open, providerName, fetchRateLimits])

  // Only render the form when the loaded rate-limits actually belong
  // to the active provider; prevents briefly rendering provider A's
  // caps while opening the drawer for provider B (and editing them
  // under the wrong name).  ``providerName`` is also folded into the
  // remount key so two providers with identical caps still get a
  // fresh form when switching between them.
  const isLoadedForActiveProvider =
    rateLimits !== null
    && providerName !== null
    && rateLimitsProviderName === providerName
  const formKey = isLoadedForActiveProvider
    ? `${providerName}/${rateLimits.requests_per_minute}/${rateLimits.concurrent_requests}`
    : 'loading'

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Rate limits"
      ariaLabel="Edit provider rate limits"
      width="compact"
    >
      <div className="flex flex-col gap-section-gap p-card">
        {error && (
          <ErrorBanner
            severity="error"
            title="Failed to load rate limits"
            description={error}
            onRetry={
              providerName
                ? () => {
                    void fetchRateLimits(providerName)
                  }
                : undefined
            }
          />
        )}
        {loading || !isLoadedForActiveProvider ? (
          <div className="flex flex-col gap-grid-gap">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <RateLimitsForm
            key={formKey}
            providerName={providerName}
            initial={rateLimits}
            onClose={onClose}
          />
        )}
      </div>
    </Drawer>
  )
}
