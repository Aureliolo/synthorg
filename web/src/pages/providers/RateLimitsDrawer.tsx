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
interface RateLimitUpdates {
  requests_per_minute?: number
  concurrent_requests?: number
}

type RateLimitValidation =
  | { ok: true; updates: RateLimitUpdates }
  | { ok: false; error: string }

// Diff the parsed caps against the persisted config; only changed
// fields land in the updates patch.
function diffRateLimitUpdates(
  rpmCap: number | null,
  concurrentCap: number | null,
  initial: RateLimitsConfig,
): RateLimitUpdates {
  const updates: RateLimitUpdates = {}
  if (rpmCap !== initial.requests_per_minute) {
    updates.requests_per_minute = rpmCap ?? 0
  }
  if (concurrentCap !== initial.concurrent_requests) {
    updates.concurrent_requests = concurrentCap ?? 0
  }
  return updates
}

// Pure validation + diff for the rate-limit form. Keeps ``handleSave``
// under the complexity cap by returning either the diffed updates or a
// single user-facing error string.
function validateRateLimits(
  rpm: string,
  concurrent: string,
  initial: RateLimitsConfig,
): RateLimitValidation {
  const rpmCap = parseCap(rpm)
  const concurrentCap = parseCap(concurrent)
  const rpmInvalid = rpmCap === null && rpm.trim() !== ''
  if (rpmInvalid) {
    return { ok: false, error: 'Requests per minute must be a non-negative integer.' }
  }
  const concurrentInvalid = concurrentCap === null && concurrent.trim() !== ''
  if (concurrentInvalid) {
    return { ok: false, error: 'Concurrent requests must be a non-negative integer.' }
  }
  const updates = diffRateLimitUpdates(rpmCap, concurrentCap, initial)
  if (Object.keys(updates).length === 0) {
    return { ok: false, error: 'No fields changed.' }
  }
  return { ok: true, updates }
}

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
    // Guard against duplicate submissions while a save is already
    // in flight; rapid clicks would otherwise issue parallel PATCH
    // requests, each writing a separate audit row.
    if (saving) return
    const validation = validateRateLimits(rpm, concurrent, initial)
    if (!validation.ok) {
      setValidationError(validation.error)
      return
    }
    setValidationError(null)
    setSaving(true)
    try {
      const result = await updateRateLimits(providerName, validation.updates)
      if (result !== null) {
        onClose()
      }
    } finally {
      // Always release ``saving`` so a one-off bug in the store
      // mutation can't leave the form permanently disabled.
      setSaving(false)
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
interface RateLimitsBodyProps {
  providerName: string | null
  rateLimits: RateLimitsConfig | null
  loading: boolean
  error: string | null
  isLoadedForActiveProvider: boolean
  onRetry: (name: string) => void
  onClose: () => void
}

function RateLimitsErrorBanner({
  show,
  providerName,
  error,
  onRetry,
}: {
  show: boolean
  providerName: string | null
  error: string | null
  onRetry: (name: string) => void
}) {
  if (!show || providerName === null) return null
  return (
    <ErrorBanner
      severity="error"
      title="Failed to load rate limits"
      description={error}
      onRetry={() => onRetry(providerName)}
    />
  )
}

function RateLimitsSkeleton() {
  return (
    <div className="flex flex-col gap-grid-gap">
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-12 w-full" />
    </div>
  )
}

function RateLimitsBody({
  providerName,
  rateLimits,
  loading,
  error,
  isLoadedForActiveProvider,
  onRetry,
  onClose,
}: RateLimitsBodyProps) {
  const showError = error !== null && isLoadedForActiveProvider

  return (
    <div className="flex flex-col gap-section-gap p-card">
      <RateLimitsErrorBanner
        show={showError}
        providerName={providerName}
        error={error}
        onRetry={onRetry}
      />
      {loading || !isLoadedForActiveProvider || rateLimits === null || providerName === null ? (
        <RateLimitsSkeleton />
      ) : (
        <RateLimitsForm
          key={`${providerName}/${rateLimits.requests_per_minute}/${rateLimits.concurrent_requests}`}
          providerName={providerName}
          initial={rateLimits}
          onClose={onClose}
        />
      )}
    </div>
  )
}

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
  // under the wrong name).
  const isLoadedForActiveProvider =
    rateLimits !== null
    && providerName !== null
    && rateLimitsProviderName === providerName
  const isActiveProvider =
    providerName !== null && rateLimitsProviderName === providerName

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Rate limits"
      ariaLabel="Edit provider rate limits"
      width="compact"
    >
      <RateLimitsBody
        providerName={providerName}
        rateLimits={rateLimits}
        loading={loading}
        error={isActiveProvider ? error : null}
        isLoadedForActiveProvider={isLoadedForActiveProvider}
        onRetry={(name) => void fetchRateLimits(name)}
        onClose={onClose}
      />
    </Drawer>
  )
}
