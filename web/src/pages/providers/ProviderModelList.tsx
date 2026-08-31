import { useMemo, useState } from 'react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { ModelStalenessBadge } from '@/components/ui/model-staleness-badge'
import { ToolCallingUnavailableBadge } from '@/components/ui/tool-calling-unavailable-badge'
import { SearchInput } from '@/components/ui/search-input'
import { cn } from '@/lib/utils'
import { Boxes, RotateCcw, Settings2, Trash2 } from 'lucide-react'
import type { ProviderModelResponse } from '@/api/types/providers'
import { reenableKey } from '@/utils/providers'

interface ProviderModelRowProps {
  model: ProviderModelResponse
  supportsDelete: boolean
  supportsConfig: boolean
  hasActions: boolean
  onDelete?: ((modelId: string) => void) | undefined
  onConfigure?: ((model: ProviderModelResponse) => void) | undefined
  onReenableToolCalling?: ((modelId: string) => void) | undefined
  // Provider-qualified pending-re-enable keys (see ``reenableKey``); model ids
  // are not unique across providers, so the pending state is matched against
  // ``reenableKey(providerName, model.id)`` rather than the bare id.
  reenablingModelIds?: ReadonlySet<string> | undefined
  providerName?: string | undefined
}

function CapabilityBadges({ model }: { model: ProviderModelResponse }) {
  // Embedding models are pure vector models: show only the embedding pill,
  // since chat/tools/reasoning/vision don't apply. For everything else the
  // absence of an embedding pill already implies a chat model, so chat earns
  // no pill -- only the extra capabilities show. Streaming is universal, so it
  // earns no badge either. Every pill keeps its own distinct hue.
  const badges: { label: string; show: boolean; className: string }[] =
    model.supports_embeddings
      ? [{ label: 'embedding', show: true, className: 'bg-danger/15 text-danger' }]
      : [
          {
            label: 'reasoning',
            show: model.supports_reasoning,
            className: 'bg-violet/15 text-violet',
          },
          {
            label: 'tools',
            show: model.supports_tools,
            className: 'bg-success/15 text-success',
          },
          {
            label: 'vision',
            show: model.supports_vision,
            className: 'bg-warning/15 text-warning',
          },
          {
            label: 'image',
            show: model.supports_image_generation,
            className: 'bg-accent/15 text-accent',
          },
          {
            label: 'cached',
            show: model.supports_prompt_caching,
            className: 'bg-info/15 text-info',
          },
        ]

  const visible = badges.filter((b) => b.show)
  if (visible.length === 0) {
    // Honest surfacing, never a guess: when neither the provider nor LiteLLM
    // reported capabilities (metadata_source 'unknown') we mark the model
    // unverified rather than implying it has none. A known-but-plain chat
    // model (source != 'unknown') simply shows no extra pills. (An embedding
    // model always yields a visible pill, so it never reaches this branch.)
    if (model.metadata_source === 'unknown') {
      return (
        <span className="rounded bg-bg-surface px-1.5 py-0.5 text-micro font-medium leading-tight text-text-muted">
          capabilities unverified
        </span>
      )
    }
    return null
  }

  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((b) => (
        <span
          key={b.label}
          className={cn('rounded px-1.5 py-0.5 text-micro font-medium leading-tight', b.className)}
        >
          {b.label}
        </span>
      ))}
    </div>
  )
}

/** Whether a model row exposes any action control. */
function rowHasActions(props: ProviderModelRowProps): boolean {
  const canReenable =
    props.onReenableToolCalling !== undefined && props.model.tool_calls_verified === false
  return props.supportsDelete || props.supportsConfig || canReenable
}

function ModelRowActions({
  model,
  supportsDelete,
  supportsConfig,
  onDelete,
  onConfigure,
  onReenableToolCalling,
  reenablingModelIds,
  providerName,
}: ProviderModelRowProps) {
  // Per-model concurrency: only this row's own in-flight re-enable disables it,
  // so re-enabling a different model leaves this button clickable.
  const isReenabling =
    providerName !== undefined &&
    reenablingModelIds !== undefined &&
    reenablingModelIds.has(reenableKey(providerName, model.id))
  return (
    <div className="flex items-center justify-end gap-1">
      {onReenableToolCalling !== undefined && model.tool_calls_verified === false && (
        /* lint-allow: id-in-ui -- a model identifier IS the word an operator
           reads: what they pick a model by and what the provider bills
           against, so it is what a screen reader should read out too. */
        <Button
          aria-label={`Re-enable tool calling for ${model.id}`}
          variant="ghost"
          size="icon"
          onClick={() => onReenableToolCalling(model.id)}
          disabled={isReenabling}
          aria-busy={isReenabling || undefined}
          title="Re-enable tool calling"
          className="size-7 text-warning hover:bg-warning/10"
        >
          <RotateCcw className="size-3.5" />
        </Button>
      )}
      {supportsConfig && (
        /* lint-allow: id-in-ui -- the model identifier is the name. */
        <Button
          aria-label={`Configure ${model.id}`}
          variant="ghost"
          size="icon"
          onClick={() => onConfigure?.(model)}
          title="Configure"
          className="size-7"
        >
          <Settings2 className="size-3.5" />
        </Button>
      )}
      {supportsDelete && (
        /* lint-allow: id-in-ui -- the model identifier is the name. */
        <Button
          aria-label={`Delete ${model.id}`}
          variant="ghost"
          size="icon"
          onClick={() => onDelete?.(model.id)}
          title="Delete"
          className="size-7 text-text-muted hover:bg-danger/10 hover:text-danger"
        >
          <Trash2 className="size-3.5" />
        </Button>
      )}
    </div>
  )
}

function ProviderModelRow(props: ProviderModelRowProps) {
  const { model } = props
  return (
    <tr>
      <td className="py-2 pr-4 font-mono text-foreground">
        <span className="inline-flex items-center gap-1.5">
          {/* lint-allow: id-in-ui -- a model identifier IS the word an operator
              reads: it is what they pick a model by and what the provider
              bills against, so there is no name behind it to resolve. */}
          {model.id}
          <ModelStalenessBadge stale={model.stale} />
          <ToolCallingUnavailableBadge toolCallsVerified={model.tool_calls_verified} />
        </span>
      </td>
      <td className="py-2 pr-4 text-text-secondary">{model.alias ?? '--'}</td>
      <td className="py-2 pr-4">
        <CapabilityBadges model={model} />
      </td>
      <td className="py-2 pr-4 text-right font-mono text-text-secondary">
        {(model.max_context / 1000).toFixed(0)}k
      </td>
      <td className="py-2 pr-4 text-right font-mono text-text-secondary">
        {model.cost_per_1k_input.toFixed(4)}
      </td>
      <td className="py-2 pr-4 text-right font-mono text-text-secondary">
        {model.cost_per_1k_output.toFixed(4)}
      </td>
      {props.hasActions && (
        <td className="py-2 text-right">
          {rowHasActions(props) ? <ModelRowActions {...props} /> : null}
        </td>
      )}
    </tr>
  )
}

interface ProviderModelListProps {
  models: readonly ProviderModelResponse[]
  supportsDelete?: boolean
  supportsConfig?: boolean
  onDelete?: ((modelId: string) => void) | undefined
  onConfigure?: ((model: ProviderModelResponse) => void) | undefined
  onReenableToolCalling?: ((modelId: string) => void) | undefined
  reenablingModelIds?: ReadonlySet<string> | undefined
  providerName?: string | undefined
}

interface ModelTableProps extends ProviderModelListProps {
  models: readonly ProviderModelResponse[]
  hasActions: boolean
  supportsDelete: boolean
  supportsConfig: boolean
}

function ModelTable({ models, hasActions, ...rest }: ModelTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-muted">
            <th className="pb-2 pr-4 font-medium">Model ID</th>
            <th className="pb-2 pr-4 font-medium">Alias</th>
            <th className="pb-2 pr-4 font-medium">Capabilities</th>
            <th className="pb-2 pr-4 font-medium text-right">Context</th>
            <th className="pb-2 pr-4 font-medium text-right">Input/1k</th>
            <th className="pb-2 pr-4 font-medium text-right">Output/1k</th>
            {hasActions && <th className="pb-2 font-medium text-right">Actions</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {models.map((model) => (
            <ProviderModelRow
              key={model.id}
              model={model}
              hasActions={hasActions}
              supportsDelete={rest.supportsDelete}
              supportsConfig={rest.supportsConfig}
              onDelete={rest.onDelete}
              onConfigure={rest.onConfigure}
              onReenableToolCalling={rest.onReenableToolCalling}
              reenablingModelIds={rest.reenablingModelIds}
              providerName={rest.providerName}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Show the search box only once the list is long enough to warrant filtering.
const MODEL_SEARCH_THRESHOLD = 8

/** Whether the table needs an Actions column for any row. */
function listHasActions(
  models: readonly ProviderModelResponse[],
  supportsDelete: boolean,
  supportsConfig: boolean,
  onReenableToolCalling: ((modelId: string) => void) | undefined,
): boolean {
  const canReenableAny =
    onReenableToolCalling !== undefined &&
    models.some((m) => m.tool_calls_verified === false)
  return supportsDelete || supportsConfig || canReenableAny
}

export function ProviderModelList({
  models,
  supportsDelete = false,
  supportsConfig = false,
  onDelete,
  onConfigure,
  onReenableToolCalling,
  reenablingModelIds,
  providerName,
}: ProviderModelListProps) {
  const hasActions = listHasActions(
    models,
    supportsDelete,
    supportsConfig,
    onReenableToolCalling,
  )
  const [query, setQuery] = useState('')

  // Show the search box only once the list is long enough to warrant filtering.
  const showSearch = models.length >= MODEL_SEARCH_THRESHOLD

  const filtered = useMemo(() => {
    // When the search input is hidden (list below threshold), ignore any
    // residual query so the user is never stuck on "No matching models"
    // with no visible way to clear the filter.
    const q = (showSearch ? query : '').trim().toLowerCase()
    if (!q) return models
    return models.filter(
      (m) => m.id.toLowerCase().includes(q) || (m.alias?.toLowerCase().includes(q) ?? false),
    )
  }, [models, query, showSearch])

  return (
    <SectionCard
      title="Models"
      icon={Boxes}
      action={
        showSearch ? (
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Filter models by id or alias"
            ariaLabel="Filter models"
            className="w-56"
          />
        ) : undefined
      }
    >
      {models.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No models configured"
          description="Use 'Discover Models' to auto-detect available models, or add them manually."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No matching models"
          description="No model id or alias matches your filter. Clear the field to see them all."
        />
      ) : (
        <ModelTable
          models={filtered}
          hasActions={hasActions}
          supportsDelete={supportsDelete}
          supportsConfig={supportsConfig}
          onDelete={onDelete}
          onConfigure={onConfigure}
          onReenableToolCalling={onReenableToolCalling}
          reenablingModelIds={reenablingModelIds}
          providerName={providerName}
        />
      )}
    </SectionCard>
  )
}
