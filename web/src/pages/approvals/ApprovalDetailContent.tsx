import { Calendar, Shield, Tag, User, type LucideIcon } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ApprovalTimeline } from './ApprovalTimeline'
import { getRiskLevelLabel, formatUrgency } from '@/utils/approvals'
import { formatDateTime } from '@/utils/format'
import type { ApprovalResponse } from '@/api/types/approvals'

function DescriptionSection({ approval }: { approval: ApprovalResponse }) {
  const isStripped = !!approval.metadata.stripped_description
  const displayText = approval.metadata.stripped_description || approval.description
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Description
        {isStripped && (
          <span className="ml-1.5 text-[10px] font-normal normal-case text-warning">(PII redacted)</span>
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
        <span className="block text-[10px] text-muted-foreground">{label}</span>
        <span className="block text-xs text-foreground">{value}</span>
      </div>
    </div>
  )
}

function ApprovalSafetyBanners({ approval }: { approval: ApprovalResponse }) {
  const classification = approval.metadata.safety_classification
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

function ApprovalMetaGrid({
  approval,
  confidenceLabel,
}: {
  approval: ApprovalResponse
  confidenceLabel: string | null
}) {
  return (
    <div className="grid grid-cols-1 gap-grid-gap rounded-lg border border-border p-card md:grid-cols-2">
      <MetaField icon={Tag} label="Action Type" value={approval.action_type} />
      <MetaField icon={Shield} label="Risk Level" value={getRiskLevelLabel(approval.risk_level)} />
      <MetaField icon={User} label="Requested By" value={approval.requested_by} />
      <MetaField icon={Calendar} label="Created" value={formatDateTime(approval.created_at)} />
      {approval.expires_at && (
        <MetaField icon={Calendar} label="Expires" value={formatUrgency(approval.seconds_remaining)} />
      )}
      {approval.decided_by && <MetaField icon={User} label="Decided By" value={approval.decided_by} />}
      {approval.decided_at && (
        <MetaField icon={Calendar} label="Decided At" value={formatDateTime(approval.decided_at)} />
      )}
      {confidenceLabel && <MetaField icon={Shield} label="Confidence" value={confidenceLabel} />}
      {approval.metadata.safety_classification && (
        <MetaField icon={Shield} label="Safety" value={approval.metadata.safety_classification} />
      )}
    </div>
  )
}

function metadataValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  return String(value ?? '')
}

function ApprovalExtraSections({ approval }: { approval: ApprovalResponse }) {
  return (
    <>
      {approval.decision_reason && (
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Reason</span>
          <p className="mt-1 rounded border border-border bg-surface p-2 text-sm text-text-secondary">
            {approval.decision_reason}
          </p>
        </div>
      )}
      {approval.task_id && (
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Linked Task
          </span>
          <p className="mt-1 font-mono text-xs text-text-secondary">{approval.task_id}</p>
        </div>
      )}
      {Object.keys(approval.metadata).length > 0 && (
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
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
}: {
  approval: ApprovalResponse
  confidenceLabel: string | null
}) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-section-gap">
      <h2 className="text-lg font-semibold text-foreground">{approval.title}</h2>
      <ApprovalSafetyBanners approval={approval} />
      {Boolean(approval.description || approval.metadata.stripped_description) && (
        <DescriptionSection approval={approval} />
      )}
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Timeline</span>
        <ApprovalTimeline approval={approval} className="mt-2" />
      </div>
      <ApprovalMetaGrid approval={approval} confidenceLabel={confidenceLabel} />
      <ApprovalExtraSections approval={approval} />
    </div>
  )
}
