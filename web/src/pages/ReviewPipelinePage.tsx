import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router'
import {
  CheckCircle,
  MinusCircle,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

import {
  decideReviewStage,
  getReviewPipeline,
  type PipelineResult,
  type ReviewStageResult,
  type StageVerdict,
} from '@/api/endpoints/clients'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { createLogger } from '@/lib/logger'

const log = createLogger('ReviewPipelinePage')

type DecideStage = (stageName: string, verdict: StageVerdict) => Promise<void>

function VerdictIcon({ verdict }: { verdict: ReviewStageResult['verdict'] }) {
  if (verdict === 'pass') {
    return <CheckCircle className="size-4 text-success" aria-label="Pass" />
  }
  if (verdict === 'fail') {
    return <XCircle className="size-4 text-danger" aria-label="Fail" />
  }
  return <MinusCircle className="size-4 text-warning" aria-label="Skip" />
}

interface ReviewPipelineState {
  pipeline: PipelineResult | null
  loading: boolean
  error: string | null
  actionError: string | null
  decisionNotice: string | null
  submitting: boolean
  handleDecide: DecideStage
}

function useReviewPipeline(taskId: string | undefined): ReviewPipelineState {
  const [pipeline, setPipeline] = useState<PipelineResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Synchronous re-entrancy guard: ``submitting`` only commits on the
  // next render, so a rapid double-click could pass an ``if (submitting)``
  // check twice before React updates it and fire two decideReviewStage
  // calls. The ref flips immediately, closing that window.
  const inFlightRef = useRef(false)

  const handleDecide = useCallback<DecideStage>(
    async (stageName, verdict) => {
      if (!taskId || inFlightRef.current) return
      inFlightRef.current = true
      setSubmitting(true)
      setActionError(null)
      // Clear any prior success notice so a stale "Recorded ..." banner
      // cannot linger beside a fresh failure from this attempt.
      setDecisionNotice(null)
      try {
        const result = await decideReviewStage(taskId, stageName, {
          verdict,
          reason: `Manual ${verdict} from dashboard`,
        })
        setDecisionNotice(`Recorded ${verdict.toUpperCase()} for ${stageName}`)
        setPipeline(result.pipeline_result)
      } catch (err) {
        log.error('decide_stage_failed', err)
        setActionError('Failed to record stage decision.')
      } finally {
        inFlightRef.current = false
        setSubmitting(false)
      }
    },
    [taskId],
  )

  useEffect(() => {
    if (!taskId) {
      const timer = setTimeout(() => {
        setError('Missing task id in URL')
        setLoading(false)
      }, 0)
      return () => clearTimeout(timer)
    }
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setPipeline(null)
      setError(null)
      setDecisionNotice(null)
      setActionError(null)
      try {
        const result = await getReviewPipeline(taskId)
        if (cancelled) return
        setPipeline(result)
      } catch (err) {
        if (cancelled) return
        log.error('get_review_pipeline_failed', err)
        setError('Failed to load review pipeline for this task.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [taskId])

  return { pipeline, loading, error, actionError, decisionNotice, submitting, handleDecide }
}

function StageResultItem({
  stage,
  submitting,
  onDecide,
}: {
  stage: ReviewStageResult
  submitting: boolean
  onDecide: DecideStage
}) {
  return (
    <li className="rounded-md border border-border bg-card-hover p-card text-sm">
      <div className="flex items-center gap-2">
        <VerdictIcon verdict={stage.verdict} />
        <span className="font-medium text-foreground">{stage.stage_name}</span>
        <span className="ml-auto text-xs text-text-secondary">{stage.duration_ms} ms</span>
      </div>
      {stage.reason && <p className="mt-2 text-text-secondary">{stage.reason}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={submitting}
          onClick={() => void onDecide(stage.stage_name, 'pass')}
        >
          Override pass
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={submitting}
          onClick={() => void onDecide(stage.stage_name, 'fail')}
        >
          Override fail
        </Button>
      </div>
    </li>
  )
}

function StageBreakdownCard({
  stages,
  actionError,
  decisionNotice,
  submitting,
  onDecide,
}: {
  stages: readonly ReviewStageResult[]
  actionError: string | null
  decisionNotice: string | null
  submitting: boolean
  onDecide: DecideStage
}) {
  return (
    <SectionCard title="Stage breakdown" icon={ShieldCheck}>
      {actionError && (
        <div className="mb-card">
          <ErrorBanner variant="section" severity="error" title="Could not complete the review step" description={actionError} />
        </div>
      )}
      {decisionNotice && (
        <div
          role="status"
          aria-live="polite"
          className="mb-card rounded-md border border-success/30 bg-success/5 p-card text-sm text-success"
        >
          {decisionNotice}
        </div>
      )}
      <ul className="space-y-3">
        {stages.map((stage) => (
          <StageResultItem
            key={stage.stage_name}
            stage={stage}
            submitting={submitting}
            onDecide={onDecide}
          />
        ))}
      </ul>
    </SectionCard>
  )
}

/**
 * Review pipeline visualization for a single task.
 *
 * Resolves the task via the review controller and renders the
 * per-stage breakdown with verdict icons, reasons, and timing.
 */
export default function ReviewPipelinePage() {
  const { taskId } = useParams<{ taskId: string }>()
  const { pipeline, loading, error, actionError, decisionNotice, submitting, handleDecide } =
    useReviewPipeline(taskId)

  if (loading) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Review Pipeline" />
        <SkeletonCard />
      </div>
    )
  }

  if (error || !pipeline) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Review Pipeline" />
        <ErrorBanner severity="error" title="Pipeline result not available" description={error ?? undefined} />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Review Pipeline" description={`Task ${pipeline.task_id}`} />

      <SectionCard title="Overall verdict" icon={ShieldCheck}>
        <div className="flex items-center gap-2 text-sm">
          <VerdictIcon verdict={pipeline.final_verdict} />
          <span className="font-medium text-foreground">
            {pipeline.final_verdict.toUpperCase()}
          </span>
          <span className="text-text-secondary">· {pipeline.total_duration_ms} ms</span>
        </div>
      </SectionCard>

      <StageBreakdownCard
        stages={pipeline.stage_results}
        actionError={actionError}
        decisionNotice={decisionNotice}
        submitting={submitting}
        onDecide={handleDecide}
      />
    </div>
  )
}
