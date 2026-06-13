import { Activity, Clock, Database, Settings } from 'lucide-react'

import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'

import { CheckpointTable } from './fine-tuning/CheckpointTable'
import { DependencyMissingBanner } from './fine-tuning/DependencyMissingBanner'
import { PipelineControlPanel } from './fine-tuning/PipelineControlPanel'
import { PipelineProgressBar } from './fine-tuning/PipelineProgressBar'
import { PipelineStepper } from './fine-tuning/PipelineStepper'
import { RunHistoryTable } from './fine-tuning/RunHistoryTable'
import { useFineTuningPageController } from './fine-tuning/useFineTuningPageController'

export default function FineTuningPage() {
  const ctrl = useFineTuningPageController()

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Embedding Fine-Tuning" />

      {ctrl.bannerError !== null && (
        <ErrorBanner
          severity="error"
          title="Could not load fine-tuning data"
          description={ctrl.bannerError}
        />
      )}

      {ctrl.hasDependencyFailure && <DependencyMissingBanner />}

      {ctrl.isInitialLoading ? <FineTuningBootstrapSkeletons /> : <FineTuningPageContent ctrl={ctrl} />}
    </div>
  )
}

function FineTuningBootstrapSkeletons() {
  return (
    <>
      <SkeletonCard header lines={3} />
      <SkeletonCard header lines={4} />
      <SkeletonCard header lines={5} />
    </>
  )
}

interface FineTuningPageContentProps {
  ctrl: ReturnType<typeof useFineTuningPageController>
}

function FineTuningPageContent({ ctrl }: FineTuningPageContentProps) {
  const activeRun = ctrl.status ? ctrl.runs.find((r) => r.id === ctrl.status?.run_id) : undefined

  return (
    <>
      <SectionCard title="Pipeline Control" icon={Settings}>
        <PipelineControlPanel />
      </SectionCard>

      {ctrl.status && (
        <SectionCard title={ctrl.isActive ? 'Progress' : 'Last pipeline run'} icon={Activity}>
          {/* Render the stepper read-only when the pipeline is idle so the
              last-known stage stays visible on tab-switch, rather than the
              progress section vanishing the moment a run settles. */}
          <PipelineStepper stage={ctrl.status.stage} />
          <PipelineProgressBar
            stage={ctrl.status.stage}
            progress={ctrl.status.progress}
            startedAt={activeRun?.started_at ?? null}
          />
        </SectionCard>
      )}

      {ctrl.showEmptyState ? (
        <SectionCard title="Checkpoints" icon={Database}>
          <EmptyState
            icon={Database}
            title="No fine-tune runs yet"
            description="Kick off a pipeline above to produce your first checkpoint. Completed runs and their checkpoints will show up here."
          />
        </SectionCard>
      ) : (
        <>
          <SectionCard title="Checkpoints" icon={Database}>
            <CheckpointTable />
          </SectionCard>

          <SectionCard title="Run History" icon={Clock}>
            <RunHistoryTable />
          </SectionCard>
        </>
      )}
    </>
  )
}
