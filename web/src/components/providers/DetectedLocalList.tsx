import { useState } from 'react'
import { AlertTriangle, Check, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { ProviderLogo } from './ProviderLogo'
import type {
  LocalPreset,
  ProbePresetResponse,
  ProviderConfig,
} from '@/api/types/providers'

const log = createLogger('detected-local-list')

/**
 * Local preset name → cloud-counterpart preset name.
 *
 * When a detected local preset has an entry in this map, its row
 * renders an additional `[Add cloud]` button that opens the
 * credential form pre-filled with the hosted variant.  Today this
 * is `ollama` → `ollama-cloud`; add more entries here when other
 * local backends gain cloud counterparts (e.g. one day
 * `'lm-studio': 'lm-studio-cloud'`).  Keep both preset names valid
 * entries in `PROVIDER_PRESETS` on the backend.
 */
const LOCAL_TO_CLOUD_COUNTERPART: Readonly<Record<string, string>> = {
  ollama: 'ollama-cloud',
}

interface DetectedLocalRowProps {
  preset: LocalPreset
  result: ProbePresetResponse | undefined
  alreadyAddedLocal: boolean
  alreadyAddedCloud: boolean
  adding: 'local' | 'cloud' | null
  onAddLocal: (presetName: string, detectedUrl: string) => void
  onAddCloud?: (cloudPresetName: string) => void
}

function DetectedLocalRow({
  preset,
  result,
  alreadyAddedLocal,
  alreadyAddedCloud,
  adding,
  onAddLocal,
  onAddCloud,
}: DetectedLocalRowProps) {
  const cloudCounterpart = LOCAL_TO_CLOUD_COUNTERPART[preset.name]
  const detectedUrl = result?.url
  const modelCount = result?.model_count ?? 0

  return (
    <div className="flex items-center gap-3 text-sm">
      <Check className="size-4 text-success" aria-hidden="true" />
      <ProviderLogo name={preset.name} size={20} />
      <div className="flex-1">
        <span className="font-medium text-foreground">{preset.display_name}</span>
        {detectedUrl && (
          <span className="ml-2 text-xs text-text-muted">
            at {detectedUrl} ({modelCount} models)
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {alreadyAddedLocal ? (
          <span className="text-xs text-success">Local added</span>
        ) : (
          detectedUrl && (
            <Button
              size="xs"
              onClick={() => onAddLocal(preset.name, detectedUrl)}
              disabled={adding !== null}
            >
              {adding === 'local' ? 'Adding...' : 'Add local'}
            </Button>
          )
        )}
        {cloudCounterpart && onAddCloud && (
          alreadyAddedCloud ? (
            <span className="text-xs text-success">Cloud added</span>
          ) : (
            <Button
              size="xs"
              variant="outline"
              onClick={() => onAddCloud(cloudCounterpart)}
              disabled={adding !== null}
            >
              {adding === 'cloud' ? 'Opening...' : 'Add cloud'}
            </Button>
          )
        )}
      </div>
    </div>
  )
}

export interface DetectedLocalListProps {
  /** Local presets that participate in auto-detect (excludes vLLM). */
  localPresets: readonly LocalPreset[]
  probeResults: Readonly<Partial<Record<string, ProbePresetResponse>>>
  /**
   * Per-preset probe failures keyed by preset name.  Populated when the
   * batch ``/providers/probe-local`` endpoint reports per-preset
   * errors (preset returned an HTTP error, timed out, etc.).  Disjoint
   * with ``probeResults`` -- a preset either succeeded or failed.
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

/**
 * "Detected on this machine" panel for local LLM servers.
 *
 * Behaviour:
 * - Hidden entirely when probing is idle and no preset returned a hit.
 * - Renders a skeleton while the batch probe is in flight.
 * - For each detected preset, a row with `[Add local]` and -- when a
 *   cloud counterpart exists (e.g. Ollama → Ollama Cloud) -- an
 *   additional `[Add cloud]` button.
 *
 * Failures are not surfaced as X marks: a preset that did not respond
 * simply does not appear.  The wizard's error banner handles top-level
 * probe failures separately.
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
  const [adding, setAdding] = useState<{ name: string; kind: 'local' | 'cloud' } | null>(null)

  // A preset is "detected" when its probe result has a URL.  Cloud
  // presets and undetected locals are absent from probeResults.
  const detectedPresets = localPresets.filter((p) => probeResults[p.name]?.url)
  // Failed presets: probe was attempted and returned an error.  Surface
  // these so the operator distinguishes "service unreachable" from
  // "preset never tried".
  const failedPresets = localPresets.filter((p) => probeErrors?.[p.name])

  if (!probing && detectedPresets.length === 0 && failedPresets.length === 0) {
    // Nothing detected, nothing failed, not currently probing.  The
    // surrounding step provides a "Re-scan" affordance via Configure
    // manually -> the wizard / Settings page own that surface.
    return null
  }

  const handleAddLocal = async (name: string, url: string) => {
    setAdding({ name, kind: 'local' })
    try {
      await onAddLocal(name, url)
    } finally {
      setAdding(null)
    }
  }

  const handleAddCloud = (cloudPresetName: string) => {
    if (!onAddCloud) return
    setAdding({ name: cloudPresetName, kind: 'cloud' })
    try {
      onAddCloud(cloudPresetName)
    } catch (err) {
      // ``onAddCloud`` opens the modal synchronously; the only way
      // it can throw is a programming bug in the caller.  Surface
      // it for debugging without leaving the row disabled forever.
      log.error('onAddCloud handler raised', getErrorMessage(err))
      setAdding(null)
      return
    }
    // Cloud add opens the modal synchronously; clear the in-flight
    // marker on the next tick so the button briefly reflects intent
    // without keeping the row disabled forever.
    setTimeout(() => setAdding(null), 0)
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-card">
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
          onClick={() => void onReprobe()}
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
      {probing && detectedPresets.length === 0 && failedPresets.length === 0 ? (
        <div className="space-y-2">
          <Skeleton className="h-6 rounded-md" />
          <Skeleton className="h-6 rounded-md" />
        </div>
      ) : (
        <>
          {detectedPresets.map((preset) => {
            const cloudCounterpart = LOCAL_TO_CLOUD_COUNTERPART[preset.name]
            const isAddingThis =
              adding && adding.name === preset.name ? adding.kind : null
            const isAddingCloudCounterpart =
              adding && cloudCounterpart && adding.name === cloudCounterpart
                ? adding.kind
                : null
            return (
              <DetectedLocalRow
                key={preset.name}
                preset={preset}
                result={probeResults[preset.name]}
                alreadyAddedLocal={preset.name in providers}
                alreadyAddedCloud={Boolean(
                  cloudCounterpart && cloudCounterpart in providers,
                )}
                adding={isAddingThis ?? isAddingCloudCounterpart}
                onAddLocal={handleAddLocal}
                onAddCloud={onAddCloud ? handleAddCloud : undefined}
              />
            )
          })}
          {failedPresets.map((preset) => (
            <div
              key={`error-${preset.name}`}
              className="flex items-center gap-3 text-sm"
              data-probe-error={preset.name}
            >
              <AlertTriangle
                className="size-4 text-warning"
                aria-hidden="true"
              />
              <ProviderLogo name={preset.name} size={20} />
              <div className="flex-1">
                <span className="font-medium text-foreground">
                  {preset.display_name}
                </span>
                <span className="ml-2 text-xs text-text-muted">
                  probe failed: {probeErrors?.[preset.name] ?? 'unknown error'}
                </span>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
