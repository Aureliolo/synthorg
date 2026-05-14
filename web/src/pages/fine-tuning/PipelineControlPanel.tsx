import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'

import { ACTIVE_STAGES } from '@/api/endpoints/fine-tuning'
import type { StartFineTuneRequest } from '@/api/endpoints/fine-tuning'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { useFineTuningStore } from '@/stores/fine-tuning'

import { PreflightResultPanel } from './PreflightResultPanel'

export function PipelineControlPanel() {
  const { status, preflight, loading, startRun, cancelRun, runPreflightCheck } =
    useFineTuningStore(useShallow((s) => ({
      status: s.status,
      preflight: s.preflight,
      loading: s.loading,
      startRun: s.startRun,
      cancelRun: s.cancelRun,
      runPreflightCheck: s.runPreflightCheck,
    })))
  const [sourceDir, setSourceDir] = useState('/data/documents')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [epochs, setEpochs] = useState('')
  const [learningRate, setLearningRate] = useState('')
  const batchSizeTouchedRef = useRef(false)
  const [batchSizeInput, setBatchSizeInput] = useState('')
  const setBatchSize = useCallback((value: string) => {
    batchSizeTouchedRef.current = true
    setBatchSizeInput(value)
  }, [])

  // When preflight arrives with a recommended batch size and the user hasn't
  // typed a value yet, use it as the initial value.
  const recommendedBatch = preflight?.recommended_batch_size
  const effectiveBatchSize =
    !batchSizeTouchedRef.current && batchSizeInput === '' && recommendedBatch != null
      ? String(recommendedBatch)
      : batchSizeInput

  // Clear stale preflight when sourceDir changes.
  useEffect(() => {
    useFineTuningStore.setState({ preflight: null })
  }, [sourceDir])

  const isActive = status != null && ACTIVE_STAGES.has(status.stage)

  const buildRequest = (): StartFineTuneRequest => {
    const request: StartFineTuneRequest = { source_dir: sourceDir }
    if (showAdvanced) {
      if (epochs !== '') {
        const parsedEpochs = Number(epochs)
        if (!Number.isNaN(parsedEpochs) && parsedEpochs > 0) request.epochs = parsedEpochs
      }
      if (learningRate !== '') {
        const parsedLr = Number(learningRate)
        if (!Number.isNaN(parsedLr) && parsedLr > 0) request.learning_rate = parsedLr
      }
      if (effectiveBatchSize !== '') {
        const parsedBatch = Number(effectiveBatchSize)
        if (!Number.isNaN(parsedBatch) && parsedBatch > 0) request.batch_size = parsedBatch
      }
    }
    return request
  }

  const handlePreflight = () => {
    void runPreflightCheck(buildRequest())
  }

  const handleStart = () => {
    void startRun(buildRequest())
  }

  return (
    <div className="flex flex-col gap-section-gap">
      {/*
       * Grid layout instead of ``flex items-end`` so the action
       * buttons align with the INPUT row of the field rather than
       * the hint line below it (previously the buttons sat one row
       * too low, visually disconnected from their input).  The
       * field's label + hint stack still flows correctly within the
       * left column.
       */}
      <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-[1fr_auto]">
        <InputField
          label="Source Directory"
          value={sourceDir}
          onValueChange={setSourceDir}
          hint="Path INSIDE the backend container -- the default /data/documents resolves to the synthorg-data Docker volume. Drop training files into that volume (or override the path here) before running pre-flight."
        />
        {/*
         * Mirror InputField's vertical stack so the button row aligns
         * with the input row (not the label above it). The empty
         * ``<span>`` reserves a label-height row using the SAME
         * Tailwind tokens InputField itself uses (``text-sm`` for label
         * size, ``gap-1.5`` for the label/input gap), so changes to
         * InputField typography keep the alignment intact without any
         * hardcoded rem offsets.
         */}
        <div className="flex flex-col gap-1.5">
          <span aria-hidden="true" className="hidden text-sm font-medium md:block">
            &nbsp;
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handlePreflight} disabled={loading}>
              Pre-flight Check
            </Button>
            {isActive ? (
              <Button variant="destructive" onClick={() => void cancelRun()}>
                Cancel
              </Button>
            ) : (
              <Button
                onClick={handleStart}
                disabled={loading || (preflight != null && !preflight.can_proceed)}
              >
                Start Fine-Tuning
              </Button>
            )}
          </div>
        </div>
      </div>

      {preflight && <PreflightResultPanel result={preflight} />}

      {/*
       * Disclosure-style toggle, but rendered as an ``outline``
       * button with a chevron so it reads as a real interactive
       * control, not body copy.  The previous ``ghost`` variant
       * stripped all borders and made the toggle look like a
       * stray text label.
       */}
      <Button
        variant="outline"
        size="sm"
        onClick={() => setShowAdvanced(!showAdvanced)}
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

      {showAdvanced && (
        <div
          id="advanced-options-panel"
          className="grid grid-cols-3 gap-grid-gap rounded-lg border border-border p-card"
        >
          <InputField label="Epochs" value={epochs} onValueChange={setEpochs} hint="Training epochs" />
          <InputField label="Learning Rate" value={learningRate} onValueChange={setLearningRate} />
          <InputField label="Batch Size" value={effectiveBatchSize} onValueChange={setBatchSize} />
        </div>
      )}
    </div>
  )
}
