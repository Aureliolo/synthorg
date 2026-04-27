import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Boxes, Settings2, Trash2 } from 'lucide-react'
import type { ProviderModelResponse } from '@/api/types/providers'

interface ProviderModelRowProps {
  model: ProviderModelResponse
  supportsDelete: boolean
  supportsConfig: boolean
  onDelete?: (modelId: string) => void
  onConfigure?: (model: ProviderModelResponse) => void
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
      <td className="py-2 pr-4 font-mono text-foreground">{model.id}</td>
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
  onDelete?: (modelId: string) => void
  onConfigure?: (model: ProviderModelResponse) => void
}

export function ProviderModelList({
  models,
  supportsDelete = false,
  supportsConfig = false,
  onDelete,
  onConfigure,
}: ProviderModelListProps) {
  const hasActions = supportsDelete || supportsConfig
  return (
    <SectionCard title="Models" icon={Boxes}>
      {models.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No models configured"
          description="Use 'Discover Models' to auto-detect available models, or add them manually."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
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
