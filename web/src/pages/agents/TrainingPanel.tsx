/**
 * Training customization panel for the agent detail/hiring flow.
 *
 * Shows training status, allows customizing sources, content types, and
 * volume caps. Displays results after training completes.
 */

import type { ReactNode } from 'react'
import { useCallback, useMemo, useState } from 'react'

import { GraduationCap } from 'lucide-react'

import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { ToggleField } from '@/components/ui/toggle-field'
import { TagInput } from '@/components/ui/tag-input'
import { cn } from '@/lib/utils'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type {
  TrainingPlanRequest,
  TrainingPlanResponse,
  TrainingResultResponse,
} from '@/api/endpoints/training'

const log = createLogger('training-panel')

const TRAINING_CONTENT_TYPES = ['procedural', 'semantic', 'tool_patterns'] as const

type TrainingContentType = (typeof TRAINING_CONTENT_TYPES)[number]

interface CustomCap {
  contentType: TrainingContentType
  cap: number
}

interface TrainingPanelProps {
  agentName: string
  plan?: TrainingPlanResponse | null
  result?: TrainingResultResponse | null
  onCreatePlan?: (overrides: TrainingPlanRequest) => void
  onExecute?: () => void
  className?: string
}

const CONTENT_TYPE_LABELS: Record<TrainingContentType, string> = {
  procedural: 'Procedural Memories',
  semantic: 'Semantic Knowledge',
  tool_patterns: 'Tool Patterns',
}

export function TrainingPanel({
  agentName,
  plan,
  result,
  onCreatePlan,
  onExecute,
  className,
}: TrainingPanelProps) {
  const cfg = useTrainingPanelConfig()
  const handleCreatePlan = () => {
    log.debug('Creating training plan', {
      agentName: sanitizeForLog(agentName),
      sourceCount: cfg.overrideSources.length,
      contentTypes: Array.from(cfg.enabledContentTypes),
    })
    onCreatePlan?.(buildPlanRequest(cfg))
  }

  return (
    <SectionCard title="Training Mode" icon={GraduationCap} className={cn(className)}>
      {plan && (
        <div className="mb-card flex items-center gap-grid-gap">
          <span className="text-sm text-muted-foreground">Status: {plan.status}</span>
        </div>
      )}
      {result && <TrainingResultSummary result={result} />}
      {!plan && <TrainingConfiguration cfg={cfg} onCreatePlan={handleCreatePlan} />}
      {plan?.status === 'pending' && (
        <Button onClick={onExecute} className="mt-card">
          Execute Training Plan
        </Button>
      )}
    </SectionCard>
  )
}

interface TrainingPanelConfig {
  overrideSources: string[]
  enabledContentTypes: Set<TrainingContentType>
  customCaps: CustomCap[]
  skipTraining: boolean
  requireReview: boolean
  setOverrideSources: (value: string[]) => void
  toggleContentType: (ct: TrainingContentType) => void
  updateCap: (ct: TrainingContentType, value: string) => void
  capsByType: Map<TrainingContentType, number>
  setSkipTraining: (value: boolean) => void
  setRequireReview: (value: boolean) => void
}

function useTrainingPanelConfig(): TrainingPanelConfig {
  const [overrideSources, setOverrideSources] = useState<string[]>([])
  const [enabledContentTypes, setEnabledContentTypes] = useState<Set<TrainingContentType>>(
    () => new Set(TRAINING_CONTENT_TYPES),
  )
  const [customCaps, setCustomCaps] = useState<CustomCap[]>([])
  const [skipTraining, setSkipTraining] = useState(false)
  const [requireReview, setRequireReview] = useState(true)

  const toggleContentType = useCallback((ct: TrainingContentType) => {
    setEnabledContentTypes((prev) => {
      const next = new Set(prev)
      if (next.has(ct)) next.delete(ct)
      else next.add(ct)
      return next
    })
  }, [])

  const updateCap = useCallback((ct: TrainingContentType, value: string) => {
    setCustomCaps((prev) => {
      const filtered = prev.filter((entry) => entry.contentType !== ct)
      if (!/^\d+$/.test(value)) return filtered
      const parsed = Number.parseInt(value, 10)
      if (parsed <= 0) return filtered
      return [...filtered, { contentType: ct, cap: parsed }]
    })
  }, [])

  const capsByType = useMemo(() => {
    const map = new Map<TrainingContentType, number>()
    for (const entry of customCaps) map.set(entry.contentType, entry.cap)
    return map
  }, [customCaps])

  return {
    overrideSources,
    enabledContentTypes,
    customCaps,
    skipTraining,
    requireReview,
    setOverrideSources,
    toggleContentType,
    updateCap,
    capsByType,
    setSkipTraining,
    setRequireReview,
  }
}

function buildPlanRequest(cfg: TrainingPanelConfig): TrainingPlanRequest {
  const contentTypes = Array.from(cfg.enabledContentTypes)
  const customCapsPayload = cfg.customCaps.length
    ? Object.fromEntries(cfg.customCaps.map(({ contentType, cap }) => [contentType, cap]))
    : undefined
  return {
    override_sources: cfg.overrideSources,
    content_types: contentTypes.length > 0 ? contentTypes : undefined,
    custom_caps: customCapsPayload,
    skip_training: cfg.skipTraining,
    require_review: cfg.requireReview,
  }
}

interface TrainingConfigurationProps {
  cfg: TrainingPanelConfig
  onCreatePlan: () => void
}

function TrainingConfiguration({ cfg, onCreatePlan }: TrainingConfigurationProps) {
  const canCreatePlan = cfg.skipTraining || cfg.enabledContentTypes.size > 0
  return (
    <div className="space-y-card">
      <div>
        <span className="mb-1 block text-sm font-medium text-foreground">
          Override Source Agents
        </span>
        <TagInput
          value={cfg.overrideSources}
          onChange={cfg.setOverrideSources}
          placeholder="Enter agent IDs..."
        />
      </div>
      <ContentTypeToggles
        enabled={cfg.enabledContentTypes}
        onToggle={cfg.toggleContentType}
      />
      <VolumeCapsSection capsByType={cfg.capsByType} onUpdateCap={cfg.updateCap} />
      <ToggleField
        label="Skip Training"
        description="Bypass the training step entirely"
        checked={cfg.skipTraining}
        onChange={cfg.setSkipTraining}
      />
      <ToggleField
        label="Require Human Review"
        description="Route training items through approval"
        checked={cfg.requireReview}
        onChange={cfg.setRequireReview}
      />
      <Button onClick={onCreatePlan} disabled={!canCreatePlan}>
        Create Training Plan
      </Button>
    </div>
  )
}

interface ContentTypeTogglesProps {
  enabled: Set<TrainingContentType>
  onToggle: (ct: TrainingContentType) => void
}

function ContentTypeToggles({ enabled, onToggle }: ContentTypeTogglesProps) {
  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-foreground">Content Types</span>
      <div className="space-y-2">
        {TRAINING_CONTENT_TYPES.map((ct) => (
          <ToggleField
            key={ct}
            label={CONTENT_TYPE_LABELS[ct]}
            checked={enabled.has(ct)}
            onChange={() => onToggle(ct)}
          />
        ))}
      </div>
    </div>
  )
}

interface VolumeCapsSectionProps {
  capsByType: Map<TrainingContentType, number>
  onUpdateCap: (ct: TrainingContentType, value: string) => void
}

function VolumeCapsSection({ capsByType, onUpdateCap }: VolumeCapsSectionProps) {
  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-foreground">
        Volume Caps (blank = default)
      </span>
      <div className="space-y-2">
        {TRAINING_CONTENT_TYPES.map((ct) => (
          <InputField
            key={ct}
            label={CONTENT_TYPE_LABELS[ct]}
            type="number"
            min={1}
            value={capsByType.get(ct)?.toString() ?? ''}
            onChange={(event) => onUpdateCap(ct, event.target.value)}
            placeholder="Use default"
          />
        ))}
      </div>
    </div>
  )
}

interface TrainingResultSummaryProps {
  result: TrainingResultResponse
}

function TrainingResultSummary({ result }: TrainingResultSummaryProps): ReactNode {
  const totalExtracted = result.items_extracted.reduce((sum, [, count]) => sum + count, 0)
  const totalStored = result.items_stored.reduce((sum, [, count]) => sum + count, 0)
  return (
    <div className="space-y-card">
      <div className="flex flex-wrap gap-grid-gap">
        <StatPill label="Sources" value={result.source_agents_used.length} />
        <StatPill label="Extracted" value={totalExtracted} />
        <StatPill label="Stored" value={totalStored} />
        {result.errors.length > 0 && (
          <StatPill label="Errors" value={result.errors.length} />
        )}
      </div>
      <ItemsByContentType items={result.items_stored} />
      {result.errors.length > 0 && <RejectionReasons errors={result.errors} />}
    </div>
  )
}

interface ItemsByContentTypeProps {
  items: TrainingResultResponse['items_stored']
}

function ItemsByContentType({ items }: ItemsByContentTypeProps) {
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium text-foreground">Items by Content Type</h4>
      {items.map(([contentType, count]) => (
        <div key={contentType} className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            {CONTENT_TYPE_LABELS[contentType as TrainingContentType] ?? contentType}
          </span>
          <span className="font-mono text-foreground">{count}</span>
        </div>
      ))}
    </div>
  )
}

interface RejectionReasonsProps {
  errors: readonly string[]
}

function RejectionReasons({ errors }: RejectionReasonsProps) {
  return (
    <div className="space-y-1">
      <h4 className="text-sm font-medium text-danger">Rejection Reasons</h4>
      {errors.map((error, idx) => (
        // eslint-disable-next-line @eslint-react/no-array-index-key -- errors can repeat
        <p key={idx} className="text-xs text-muted-foreground">
          {error}
        </p>
      ))}
    </div>
  )
}
