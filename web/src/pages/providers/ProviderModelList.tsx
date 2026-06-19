import { useMemo, useState } from 'react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { ModelStalenessBadge } from '@/components/ui/model-staleness-badge'
import { SearchInput } from '@/components/ui/search-input'
import { cn } from '@/lib/utils'
import { Boxes, Settings2, Trash2 } from 'lucide-react'
import type { ProviderModelResponse } from '@/api/types/providers'

interface ProviderModelRowProps {
  model: ProviderModelResponse
  supportsDelete: boolean
  supportsConfig: boolean
  onDelete?: ((modelId: string) => void) | undefined
  onConfigure?: ((model: ProviderModelResponse) => void) | undefined
}

function CapabilityBadges({ model }: { model: ProviderModelResponse }) {
  const badges: { label: string; show: boolean; className: string }[] = [
    {
      label: 'tools',
      show: model.supports_tools,
      className: 'bg-accent/10 text-accent',
    },
    {
      label: 'vision',
      show: model.supports_vision,
      className: 'bg-success/10 text-success',
    },
    {
      label: 'stream',
      show: model.supports_streaming,
      className: 'bg-text-muted/10 text-text-secondary',
    },
  ]

  const visible = badges.filter((b) => b.show)
  if (visible.length === 0) return null

  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((b) => (
        <span
          key={b.label}
          className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium leading-tight', b.className)}
        >
          {b.label}
        </span>
      ))}
    </div>
  )
}

function ProviderModelRow({ model, supportsDelete, supportsConfig, onDelete, onConfigure }: ProviderModelRowProps) {
  const hasActions = supportsDelete || supportsConfig
  return (
    <tr>
      <td className="py-2 pr-4 font-mono text-foreground">
        <span className="inline-flex items-center gap-1.5">
          {model.id}
          <ModelStalenessBadge stale={model.stale} />
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
      {hasActions && (
        <td className="py-2 text-right">
          <div className="flex items-center justify-end gap-1">
            {supportsConfig && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onConfigure?.(model)}
                title="Configure"
                aria-label={`Configure ${model.id}`}
                className="size-7"
              >
                <Settings2 className="size-3.5" />
              </Button>
            )}
            {supportsDelete && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onDelete?.(model.id)}
                title="Delete"
                aria-label={`Delete ${model.id}`}
                className="size-7 text-text-muted hover:bg-danger/10 hover:text-danger"
              >
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </div>
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
}

// Show the search box only once the list is long enough to warrant filtering.
const MODEL_SEARCH_THRESHOLD = 8

export function ProviderModelList({
  models,
  supportsDelete = false,
  supportsConfig = false,
  onDelete,
  onConfigure,
}: ProviderModelListProps) {
  const hasActions = supportsDelete || supportsConfig
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
              {filtered.map((model) => (
                <ProviderModelRow
                  key={model.id}
                  model={model}
                  supportsDelete={supportsDelete}
                  supportsConfig={supportsConfig}
                  onDelete={onDelete}
                  onConfigure={onConfigure}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  )
}
