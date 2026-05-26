/** Single detected-preset row inside the DetectedLocalList panel. */

import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ProviderLogo } from './ProviderLogo'
import { LOCAL_TO_CLOUD_COUNTERPART, type AddingKind } from './detected-local-utils'
import type { LocalPreset, ProbePresetResponse } from '@/api/types'

export interface DetectedLocalRowProps {
  preset: LocalPreset
  result: ProbePresetResponse | undefined
  alreadyAddedLocal: boolean
  alreadyAddedCloud: boolean
  adding: AddingKind | null
  onAddLocal: (presetName: string, detectedUrl: string) => void
  onAddCloud?: (cloudPresetName: string) => void
}

function AddLocalCell({
  alreadyAddedLocal,
  detectedUrl,
  adding,
  presetName,
  onAddLocal,
}: {
  alreadyAddedLocal: boolean
  detectedUrl: string | undefined
  adding: AddingKind | null
  presetName: string
  onAddLocal: (presetName: string, detectedUrl: string) => void
}) {
  if (alreadyAddedLocal) {
    return <span className="text-xs text-success">Local added</span>
  }
  if (!detectedUrl) return null
  return (
    <Button
      size="xs"
      onClick={() => onAddLocal(presetName, detectedUrl)}
      disabled={adding !== null}
    >
      {adding === 'local' ? 'Adding...' : 'Add local'}
    </Button>
  )
}

function AddCloudCell({
  cloudCounterpart,
  alreadyAddedCloud,
  adding,
  onAddCloud,
}: {
  cloudCounterpart: string | undefined
  alreadyAddedCloud: boolean
  adding: AddingKind | null
  onAddCloud?: (cloudPresetName: string) => void
}) {
  if (!cloudCounterpart || !onAddCloud) return null
  if (alreadyAddedCloud) {
    return <span className="text-xs text-success">Cloud added</span>
  }
  return (
    <Button
      size="xs"
      variant="outline"
      onClick={() => onAddCloud(cloudCounterpart)}
      disabled={adding !== null}
    >
      {adding === 'cloud' ? 'Opening...' : 'Add cloud'}
    </Button>
  )
}

export function DetectedLocalRow({
  preset,
  result,
  alreadyAddedLocal,
  alreadyAddedCloud,
  adding,
  onAddLocal,
  onAddCloud,
}: DetectedLocalRowProps) {
  const cloudCounterpart = LOCAL_TO_CLOUD_COUNTERPART[preset.name]
  const detectedUrl = result?.url ?? undefined
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
        <AddLocalCell
          alreadyAddedLocal={alreadyAddedLocal}
          detectedUrl={detectedUrl}
          adding={adding}
          presetName={preset.name}
          onAddLocal={onAddLocal}
        />
        <AddCloudCell
          cloudCounterpart={cloudCounterpart}
          alreadyAddedCloud={alreadyAddedCloud}
          adding={adding}
          onAddCloud={onAddCloud}
        />
      </div>
    </div>
  )
}
