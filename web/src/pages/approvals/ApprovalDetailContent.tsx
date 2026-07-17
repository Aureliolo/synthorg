import { Link } from 'react-router'
import {
  Calendar,
  ChevronRight,
  FolderKanban,
  Hourglass,
  ListChecks,
  ListTree,
  Package,
  Shield,
  Tag,
  User,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { ContentTypeBadge } from '@/components/ui/content-type-badge'
import { RunOutcomeBadge } from '@/components/ui/run-outcome-badge'
import { TaskProgress } from '@/components/ui/task-progress'
import { useTaskProgress } from '@/hooks/useTaskProgress'
import { ROUTES } from '@/router/routes'
import { DecisionOptionsSection } from './ApprovalDecisionOptions'
import { ApprovalTimeline } from './ApprovalTimeline'
import {
  getApprovalStepLabel,
  getRiskLevelLabel,
  formatUrgency,
  isFailedApproval,
} from '@/utils/approvals'
import { formatDateTime, formatFileSize } from '@/utils/format'
import { planDetailPath } from '@/utils/plans'
import type { ApprovalArtifactRef, ApprovalResponse } from '@/api/types/approvals'

const TOOL_CREATION_ACTION_TYPE = 'proposal:tool_creation'

/**
 * Task statuses for which a run is genuinely in flight, so live progress is
 * worth streaming. A ``created`` task has NOT started, e.g. a plan-review
 * approval whose task is parked until the plan is approved, so it must not be
 * treated as running (that showed a misleading "Starting run" bar on a plan
 * that was actually waiting on the operator). A terminal / in-review task
 * shows its produced output instead.
 */
const RUNNING_TASK_STATUSES: ReadonlySet<string> = new Set([
  'assigned',
  'in_progress',
  'awaiting_input',
])

/**
 * Live execution progress for the task this approval spawned, while it runs.
 *
 * Fills the gap between approving proposed work and its completion review: the
 * operator watches the run stream its steps instead of a silent queue. Only
 * shown while the task is genuinely in flight; a pending approval with nothing
 * running (a plan parked for approval spends nothing until approved) says so
 * rather than streaming a placeholder bar. Subscription is owner-gated
 * server-side, so a stream the operator cannot access simply renders nothing.
 */
function LiveProgressSection({ approval }: { approval: ApprovalResponse }) {
  const task = approval.task
  const taskId =
    task !== null && RUNNING_TASK_STATUSES.has(task.status) ? task.id : null
  const progress = useTaskProgress(taskId)
  if (progress === null) {
    if (approval.status !== 'pending') return null
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-secondary">
        <Hourglass className="size-4 shrink-0 text-accent" aria-hidden="true" />
        Waiting for your decision. Nothing runs, and no budget is spent, until
        you approve.
      </div>
    )
  }
  return (
    <div>
      <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        Live progress
      </span>
      <TaskProgress
        status={progress.status}
        stages={progress.stages}
        className="mt-2"
      />
    </div>
  )
}

/**
 * Deep-link into the full plan-review workspace for a plan-approval gate. The
 * decision buttons approve or reject in place, but the operator often wants to
 * read the whole plan (items, stakes, critical path) first; the backend stamps
 * the durable plan id into the approval metadata for exactly this jump.
 */
function PlanReviewLinkSection({ approval }: { approval: ApprovalResponse }) {
  if (approval.source !== 'plan_review') return null
  const planId = approval.metadata['plan_id']
  if (!planId) return null
  return (
    <Link
      to={planDetailPath(planId)}
      className="flex items-center justify-between gap-2 rounded-lg border border-accent/40 bg-accent/[0.04] p-card transition-colors hover:bg-accent/[0.08]"
    >
      <span className="flex min-w-0 items-center gap-2 text-sm">
        <ListTree className="size-4 shrink-0 text-accent" aria-hidden="true" />
        <span className="font-medium text-foreground">Open the full plan</span>
        <span className="truncate text-text-secondary">
          Review every item, its stakes, and the critical path before deciding.
        </span>
      </span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </Link>
  )
}

function taskDetailPath(taskId: string): string {
  return ROUTES.TASK_DETAIL.replace(':taskId', encodeURIComponent(taskId))
}

function artifactDetailPath(artifactId: string): string {
  return ROUTES.ARTIFACT_DETAIL.replace(':artifactId', encodeURIComponent(artifactId))
}

function DescriptionSection({ approval }: { approval: ApprovalResponse }) {
  const isStripped = !!approval.metadata['stripped_description']
  const displayText = approval.metadata['stripped_description'] || approval.description
  return (
    <div>
      <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        Description
        {isStripped && (
          <span className="ml-1.5 text-micro font-normal normal-case text-warning">(PII redacted)</span>
        )}
      </span>
      <p className="mt-1 text-sm text-text-secondary">{displayText}</p>
    </div>
  )
}

function MetaField({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="mt-0.5 size-3.5 text-muted-foreground" aria-hidden="true" />
      <div>
        <span className="block text-micro text-muted-foreground">{label}</span>
        <span className="block text-xs text-foreground">{value}</span>
      </div>
    </div>
  )
}

function ApprovalSafetyBanners({ approval }: { approval: ApprovalResponse }) {
  const classification = approval.metadata['safety_classification']
  return (
    <>
      {classification === 'blocked' && (
        <ErrorBanner
          variant="inline"
          severity="error"
          title="Safety classifier blocked this action"
          description="Review the details carefully before overriding."
        />
      )}
      {classification === 'suspicious' && (
        <ErrorBanner
          variant="inline"
          severity="warning"
          title="Safety classifier flagged this action as suspicious"
          description="Inspect the action payload before approving."
        />
      )}
    </>
  )
}

/** Danger banner for a failed run: what happened + what the buttons do. */
function ApprovalRunFailureBanner({ approval }: { approval: ApprovalResponse }) {
  if (!isFailedApproval(approval)) return null
  return (
    <ErrorBanner
      variant="inline"
      severity="error"
      title="This run failed"
      description={
        approval.decision_reason ??
        'The agent did not complete the task. Acknowledge to close it, or Retry to send it back for rework.'
      }
    />
  )
}

function ProducedArtifactRow({ artifact }: { artifact: ApprovalArtifactRef }) {
  return (
    <li>
      <Link
        to={artifactDetailPath(artifact.id)}
        className="flex items-center justify-between gap-2 rounded border border-border bg-surface px-2 py-1.5 text-xs transition-colors hover:border-bright hover:bg-card-hover"
      >
        <span className="flex min-w-0 items-center gap-2">
          <ContentTypeBadge contentType={artifact.content_type} />
          <span className="truncate font-mono text-text-secondary">{artifact.path}</span>
        </span>
        <span className="shrink-0 text-muted-foreground">{formatFileSize(artifact.size_bytes)}</span>
      </Link>
    </li>
  )
}

/**
 * "What was produced": the reviewer's evidence. Lists produced artifacts
 * (click through to full content) or an explicit empty/failed state, so an
 * approval is never a black box.
 */
function ProducedOutputSection({ approval }: { approval: ApprovalResponse }) {
  const run = approval.run
  if (run === null) return null
  const failed = run.outcome === 'failed'
  const extra = run.produced_artifact_count - run.artifacts.length
  return (
    <div>
      <span className="flex items-center gap-1.5 text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        <Package className="size-3.5" aria-hidden="true" />
        What was produced
        <RunOutcomeBadge outcome={run.outcome} className="ml-1" />
      </span>
      {run.artifacts.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {run.artifacts.map((artifact) => (
            <ProducedArtifactRow key={artifact.id} artifact={artifact} />
          ))}
          {extra > 0 && (
            <li className="text-micro text-muted-foreground">+ {extra} more</li>
          )}
        </ul>
      ) : (
        <div className="mt-2">
          <EmptyState
            icon={Package}
            title={failed ? 'Run failed with no output' : 'No artifacts produced'}
            description={
              failed
                ? 'This run failed before producing any artifacts.'
                : 'This run reached review but produced no artifacts.'
            }
          />
        </div>
      )}
    </div>
  )
}

/** The agent's own account of the run: narrative + step-by-step trace. */
function EvidenceSection({ approval }: { approval: ApprovalResponse }) {
  const evidence = approval.evidence_package
  if (evidence === null) return null
  const hasTrace = evidence.reasoning_trace.length > 0
  if (!evidence.narrative && !hasTrace) return null
  return (
    <div>
      <span className="flex items-center gap-1.5 text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        <ListChecks className="size-3.5" aria-hidden="true" />
        Agent account
      </span>
      {evidence.narrative && (
        <p className="mt-1 text-sm text-text-secondary">{evidence.narrative}</p>
      )}
      {hasTrace && (
        <ol className="mt-2 list-decimal space-y-1 pl-4 text-xs text-text-secondary">
          {/* Static, immutable trace: the list never reorders or mutates, so
              a positional index is a stable key here. */}
          {evidence.reasoning_trace.map((line, index) => (
            // eslint-disable-next-line @eslint-react/no-array-index-key
            <li key={`trace-${index}`}>{line}</li>
          ))}
        </ol>
      )}
    </div>
  )
}

function ApprovalMetaGrid({
  approval,
  confidenceLabel,
}: {
  approval: ApprovalResponse
  confidenceLabel: string | null
}) {
  const safety = approval.metadata['safety_classification']
  return (
    <div className="grid grid-cols-1 gap-grid-gap rounded-lg border border-border p-card md:grid-cols-2">
      <MetaField icon={Tag} label="Step" value={getApprovalStepLabel(approval)} />
      <MetaField icon={Shield} label="Risk Level" value={getRiskLevelLabel(approval.risk_level)} />
      <MetaField
        icon={User}
        label="Agent"
        value={approval.agent?.name ?? approval.requested_by}
      />
      {approval.project && (
        <MetaField icon={FolderKanban} label="Project" value={approval.project.name} />
      )}
      <MetaField icon={Calendar} label="Created" value={formatDateTime(approval.created_at)} />
      {approval.expires_at && (
        <MetaField icon={Calendar} label="Expires" value={formatUrgency(approval.seconds_remaining)} />
      )}
      <ApprovalDecisionFields approval={approval} confidenceLabel={confidenceLabel} />
      {safety && <MetaField icon={Shield} label="Safety" value={safety} />}
    </div>
  )
}

/** The decision-time meta fields (only present once an approval is decided). */
function ApprovalDecisionFields({
  approval,
  confidenceLabel,
}: {
  approval: ApprovalResponse
  confidenceLabel: string | null
}) {
  return (
    <>
      {approval.decided_by && <MetaField icon={User} label="Decided By" value={approval.decided_by} />}
      {approval.decided_at && (
        <MetaField icon={Calendar} label="Decided At" value={formatDateTime(approval.decided_at)} />
      )}
      {confidenceLabel && <MetaField icon={Shield} label="Confidence" value={confidenceLabel} />}
    </>
  )
}

/**
 * Reviewable summary of the concrete tool a `proposal:tool_creation` approval
 * would author. The backend stamps these fields into the approval metadata, so
 * an operator sees the tool name, capability, and description before approving
 * (approving makes it go live via the toolsmith approve-to-live consumer).
 */
function ProposedToolSection({ approval }: { approval: ApprovalResponse }) {
  if (approval.action_type !== TOOL_CREATION_ACTION_TYPE) return null
  const name = approval.metadata['tool_name']
  const capability = approval.metadata['tool_capability']
  const description = approval.metadata['tool_description']
  if (!name && !capability && !description) return null
  return (
    <div className="rounded-lg border border-border p-card">
      <span className="flex items-center gap-1.5 text-compact font-semibold uppercase tracking-wider text-muted-foreground">
        <Wrench className="size-3.5" aria-hidden="true" />
        Proposed tool
      </span>
      <div className="mt-2 space-y-1">
        {name && <MetaField icon={Tag} label="Tool" value={name} />}
        {capability && <MetaField icon={Shield} label="Capability" value={capability} />}
        {description && (
          <p className="mt-1 text-sm text-text-secondary">{description}</p>
        )}
      </div>
    </div>
  )
}

function metadataValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  return ''
}

function ApprovalExtraSections({ approval }: { approval: ApprovalResponse }) {
  return (
    <>
      {approval.decision_reason && (
        <div>
          <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">Reason</span>
          <p className="mt-1 rounded border border-border bg-surface p-2 text-sm text-text-secondary">
            {approval.decision_reason}
          </p>
        </div>
      )}
      {approval.task_id && (
        <div>
          <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">
            Linked Task
          </span>
          <Link
            to={taskDetailPath(approval.task_id)}
            className="mt-1 block text-xs text-accent hover:underline"
          >
            {approval.task?.title ?? approval.task_id}
          </Link>
        </div>
      )}
      {Object.keys(approval.metadata).length > 0 && (
        <div>
          <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">
            Metadata
          </span>
          <dl className="mt-1 space-y-1">
            {Object.entries(approval.metadata).map(([key, value]) => (
              <div key={key} className="flex items-center gap-2 text-xs">
                <dt className="font-mono text-muted-foreground">{key}:</dt>
                <dd className="text-text-secondary">{metadataValue(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </>
  )
}

export function ApprovalDetailContent({
  approval,
  confidenceLabel,
  chosenOptionId = null,
  onChooseOption,
}: {
  approval: ApprovalResponse
  confidenceLabel: string | null
  /** Selected decision-option id, for a decision-fork approval. */
  chosenOptionId?: string | null
  /** Picks a decision option; omit to render the options read-only. */
  onChooseOption?: (id: string) => void
}) {
  return (
    <div className="flex-1 overflow-y-auto p-card space-y-section-gap">
      <h2 className="text-lg font-semibold text-foreground">
        {approval.task?.title ?? approval.title}
      </h2>
      <ApprovalRunFailureBanner approval={approval} />
      <ApprovalSafetyBanners approval={approval} />
      {/* Key by task id so a different task remounts the section, resetting the
          accumulated progress instead of showing the prior task's stages for a
          frame while the new stream connects. */}
      <LiveProgressSection key={approval.task?.id ?? 'no-task'} approval={approval} />
      <PlanReviewLinkSection approval={approval} />
      {Boolean(approval.description || approval.metadata['stripped_description']) && (
        <DescriptionSection approval={approval} />
      )}
      <DecisionOptionsSection
        approval={approval}
        chosenOptionId={chosenOptionId}
        onChooseOption={onChooseOption}
      />
      <ProducedOutputSection approval={approval} />
      <EvidenceSection approval={approval} />
      <div>
        <span className="text-compact font-semibold uppercase tracking-wider text-muted-foreground">Timeline</span>
        <ApprovalTimeline approval={approval} className="mt-2" />
      </div>
      <ProposedToolSection approval={approval} />
      <ApprovalMetaGrid approval={approval} confidenceLabel={confidenceLabel} />
      <ApprovalExtraSections approval={approval} />
    </div>
  )
}
