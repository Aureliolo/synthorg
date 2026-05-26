import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ProviderLogo } from './ProviderLogo'
import { DetectedLocalRow } from './DetectedLocalRow'
import { LOCAL_TO_CLOUD_COUNTERPART } from './detected-local-utils'
import { useAddInFlight } from './useAddInFlight'
import type {
  LocalPreset,
  ProbePresetResponse,
  ProviderConfig,
} from '@/api/types/providers'

const log = createLogger('detected-local-list')

export interface DetectedLocalListProps {
  /** Local presets that participate in auto-detect (excludes vLLM). */
  localPresets: readonly LocalPreset[]
  probeResults: Readonly<Partial<Record<string, ProbePresetResponse>>>
  /**
   * Per-preset probe failures keyed by preset name. Populated when the
   * batch ``/providers/probe-local`` endpoint reports per-preset
   * errors (preset returned an HTTP error, timed out, etc.). Disjoint
   * with ``probeResults``: a preset either succeeded or failed.
   * Surfaced inline so the operator sees that a probe was attempted
   * and reachable to the API even if it didn't yield a working URL.
   */
  probeErrors?: Readonly<Partial<Record<string, string>>>
  probing: boolean
  providers: Readonly<Record<string, ProviderConfig>>
  onAddLocal: (presetName: string, detectedUrl: string) => void | Promise<void>
  /** Open the credential form pre-filled with the cloud counterpart preset. */
  onAddCloud?: (cloudPresetName: string) => void
  onReprobe: () => void | Promise<void>
}

function DetectedHeader({ probing, onReprobe }: { probing: boolean; onReprobe: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">
          {probing ? 'Detecting local providers...' : 'Detected on this machine'}
        </h3>
        <p className="text-xs text-text-muted">
          Auto-detected LLM servers running on this host.
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={onReprobe}
        disabled={probing}
        aria-label="Re-scan local providers"
      >
        {probing ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="size-3.5" aria-hidden="true" />
        )}
      </Button>
    </div>
  )
}

function DetectedFailedRow({
  preset,
  errorMessage,
}: {
  preset: LocalPreset
  errorMessage: string
}) {
  return (
    <div
      className="flex items-center gap-3 text-sm"
      data-probe-error={preset.name}
    >
      <AlertTriangle className="size-4 text-warning" aria-hidden="true" />
      <ProviderLogo name={preset.name} size={20} />
      <div className="flex-1">
        <span className="font-medium text-foreground">{preset.display_name}</span>
        <span className="ml-2 text-xs text-text-muted">
          probe failed: {errorMessage}
        </span>
      </div>
    </div>
  )
}

function _detectedPresets(
  localPresets: readonly LocalPreset[],
  probeResults: Readonly<Partial<Record<string, ProbePresetResponse>>>,
): readonly LocalPreset[] {
  return localPresets.filter((p) => probeResults[p.name]?.url)
}

function _failedPresets(
  localPresets: readonly LocalPreset[],
  probeErrors: Readonly<Partial<Record<string, string>>> | undefined,
): readonly LocalPreset[] {
  // Use key presence (not truthiness) so an empty-string error
  // message, a legitimate "probe failed but no detail" envelope,
  // still surfaces as a warning row instead of being silently dropped.
  if (probeErrors === undefined) return []
  return localPresets.filter((p) =>
    Object.prototype.hasOwnProperty.call(probeErrors, p.name),
  )
}

function _resolveRowAdding(
  adding: Readonly<Record<string, 'local' | 'cloud'>>,
  presetName: string,
  cloudCounterpart: string | undefined,
): 'local' | 'cloud' | null {
  return adding[presetName] ?? (cloudCounterpart ? adding[cloudCounterpart] : undefined) ?? null
}

/**
 * "Detected on this machine" panel for local LLM servers.
 *
 * Behaviour:
 * - Hidden entirely when probing is idle, no preset returned a hit,
 *   AND no preset failed.
 * - Renders a skeleton while the batch probe is in flight.
 * - For each detected preset, a row with `[Add local]` and, when a
 *   cloud counterpart exists (e.g. Ollama -> Ollama Cloud), an
 *   additional `[Add cloud]` button.
 * - For each preset whose probe raised, a warning row (AlertTriangle
 *   icon + display name + redacted error message) so the operator
 *   distinguishes "service unreachable" from "preset never tried".
 *   Top-level batch failures are surfaced separately by the wizard /
 *   Settings page above this component.
 */
export function DetectedLocalList({
  localPresets,
  probeResults,
  probeErrors,
  probing,
  providers,
  onAddLocal,
  onAddCloud,
  onReprobe,
}: DetectedLocalListProps) {
  const { adding, startAdd, finishAdd } = useAddInFlight()
  const detected = _detectedPresets(localPresets, probeResults)
  const failed = _failedPresets(localPresets, probeErrors)

  if (!probing && detected.length === 0 && failed.length === 0) {
    // Nothing detected, nothing failed, not currently probing. The
    // surrounding step provides a "Re-scan" affordance via Configure
    // manually -> the wizard / Settings page own that surface.
    return null
  }

  const handleAddLocal = async (name: string, url: string): Promise<void> => {
    if (!startAdd(name, 'local')) return
    try {
      await onAddLocal(name, url)
    } finally {
      finishAdd(name)
    }
  }

  const handleAddCloud = (cloudPresetName: string): void => {
    if (!onAddCloud) return
    if (!startAdd(cloudPresetName, 'cloud')) return
    try {
      onAddCloud(cloudPresetName)
    } catch (err) {
      // ``onAddCloud`` opens the modal synchronously; the only way it
      // can throw is a programming bug in the caller. Surface it for
      // debugging without leaving the row disabled forever. Use
      // ``sanitizeForLog`` so any attacker-controlled string in the
      // caller's error path is truncated, control-char stripped, and
      // bidi-override safe.
      log.error('onAddCloud handler raised', sanitizeForLog(err))
      finishAdd(cloudPresetName)
      return
    }
    // Cloud add opens the modal synchronously; defer the in-flight
    // marker clear to a microtask so the button briefly reflects
    // intent without holding the row disabled. ``setTimeout(..., 0)``
    // would register a Timeout handle with Node's async_hooks tracker
    // that the active-handle gate (allowlist is empty, per web/CLAUDE.md)
    // would flag as a leak; ``queueMicrotask`` schedules on the same
    // tick without an event-loop handle.
    queueMicrotask(() => { finishAdd(cloudPresetName) })
  }

  const showSkeleton = probing && detected.length === 0 && failed.length === 0

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-card">
      <DetectedHeader probing={probing} onReprobe={() => void onReprobe()} />
      {showSkeleton ? (
        <div className="space-y-2">
          <Skeleton className="h-6 rounded-md" />
          <Skeleton className="h-6 rounded-md" />
        </div>
      ) : (
        <>
          {detected.map((preset) => {
            const cloudCounterpart = LOCAL_TO_CLOUD_COUNTERPART[preset.name]
            return (
              <DetectedLocalRow
                key={preset.name}
                preset={preset}
                result={probeResults[preset.name]}
                alreadyAddedLocal={preset.name in providers}
                alreadyAddedCloud={Boolean(
                  cloudCounterpart && cloudCounterpart in providers,
                )}
                adding={_resolveRowAdding(adding, preset.name, cloudCounterpart)}
                onAddLocal={(name, url) => { void handleAddLocal(name, url) }}
                onAddCloud={onAddCloud ? handleAddCloud : undefined}
              />
            )
          })}
          {failed.map((preset) => (
            <DetectedFailedRow
              key={`error-${preset.name}`}
              preset={preset}
              errorMessage={probeErrors?.[preset.name] ?? 'unknown error'}
            />
          ))}
        </>
      )}
    </div>
  )
}
