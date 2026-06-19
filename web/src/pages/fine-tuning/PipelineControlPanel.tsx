import { ChevronDown, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'

import { PreflightResultPanel } from './PreflightResultPanel'
import { usePipelineControlState } from './usePipelineControlState'

export function PipelineControlPanel() {
  const ctrl = usePipelineControlState()

  return (
    <div className="flex flex-col gap-section-gap">
      <SourceDirectoryRow
        sourceDir={ctrl.sourceDir}
        loading={ctrl.loading}
        isActive={ctrl.isActive}
        startDisabled={ctrl.startDisabled}
        onSourceDirChange={ctrl.setSourceDir}
        onPreflight={ctrl.handlePreflight}
        onStart={ctrl.handleStart}
        onCancel={ctrl.handleCancel}
      />

      {ctrl.showPreflightPanel && ctrl.preflight && (
        <PreflightResultPanel result={ctrl.preflight} />
      )}

      <AdvancedToggle showAdvanced={ctrl.showAdvanced} onToggle={ctrl.toggleAdvanced} />

      {ctrl.showAdvanced && (
        <AdvancedOptionsPanel
          epochs={ctrl.epochs}
          learningRate={ctrl.learningRate}
          effectiveBatchSize={ctrl.effectiveBatchSize}
          onEpochsChange={ctrl.setEpochs}
          onLearningRateChange={ctrl.setLearningRate}
          onBatchSizeChange={ctrl.setBatchSize}
        />
      )}
    </div>
  )
}

interface SourceDirectoryRowProps {
  sourceDir: string
  loading: boolean
  isActive: boolean
  startDisabled: boolean
  onSourceDirChange: (value: string) => void
  onPreflight: () => void
  onStart: () => void
  onCancel: () => void
}

function SourceDirectoryRow({
  sourceDir,
  loading,
  isActive,
  startDisabled,
  onSourceDirChange,
  onPreflight,
  onStart,
  onCancel,
}: SourceDirectoryRowProps) {
  // Grid layout (vs `flex items-end`) so the action buttons align with the
  // INPUT row of the field rather than the hint line below it.
  return (
    <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-[1fr_auto]">
      <InputField
        label="Source Directory"
        value={sourceDir}
        onValueChange={onSourceDirChange}
        hint="Path INSIDE the backend container: the default /data/documents resolves to the synthorg-data Docker volume. Drop training files into that volume (or override the path here) before running pre-flight."
      />
      <div className="flex flex-col gap-1.5">
        {/* Reserve a label-height row so the button row aligns with the input. */}
        <span aria-hidden="true" className="hidden text-sm font-medium md:block">
          &nbsp;
        </span>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onPreflight} disabled={loading}>
            Pre-flight Check
          </Button>
          {isActive ? (
            <Button variant="destructive" onClick={onCancel}>
              Cancel
            </Button>
          ) : (
            <Button onClick={onStart} disabled={startDisabled}>
              Start Fine-Tuning
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

interface AdvancedToggleProps {
  showAdvanced: boolean
  onToggle: () => void
}

function AdvancedToggle({ showAdvanced, onToggle }: AdvancedToggleProps) {
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onToggle}
      className="self-start gap-1.5"
      aria-expanded={showAdvanced}
      aria-controls="advanced-options-panel"
    >
      {showAdvanced ? (
        <ChevronDown className="size-3.5" aria-hidden="true" />
      ) : (
        <ChevronRight className="size-3.5" aria-hidden="true" />
      )}
      {showAdvanced ? 'Hide' : 'Show'} Advanced Options
    </Button>
  )
}

interface AdvancedOptionsPanelProps {
  epochs: string
  learningRate: string
  effectiveBatchSize: string
  onEpochsChange: (value: string) => void
  onLearningRateChange: (value: string) => void
  onBatchSizeChange: (value: string) => void
}

function AdvancedOptionsPanel({
  epochs,
  learningRate,
  effectiveBatchSize,
  onEpochsChange,
  onLearningRateChange,
  onBatchSizeChange,
}: AdvancedOptionsPanelProps) {
  return (
    <div
      id="advanced-options-panel"
      className="grid grid-cols-3 gap-grid-gap rounded-lg border border-border p-card max-[767px]:grid-cols-1"
    >
      <InputField
        label="Epochs"
        value={epochs}
        onValueChange={onEpochsChange}
        hint="Training epochs"
      />
      <InputField
        label="Learning Rate"
        value={learningRate}
        onValueChange={onLearningRateChange}
      />
      <InputField
        label="Batch Size"
        value={effectiveBatchSize}
        onValueChange={onBatchSizeChange}
      />
    </div>
  )
}
