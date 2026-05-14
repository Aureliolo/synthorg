import { useEffect } from 'react'
import { motion } from 'motion/react'
import { FileCode2 } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { StatPill } from '@/components/ui/stat-pill'
import { useWorkflowsStore } from '@/stores/workflows'
import { cardEntrance, staggerChildren } from '@/lib/motion'
import { formatLabel } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { BlueprintInfo } from '@/api/types/workflows'

export interface BlueprintPickerProps {
  selectedBlueprint: string | null
  onSelect: (name: string | null) => void
  workflowTypeFilter?: string | null
}

export interface BlueprintCardProps {
  blueprint: BlueprintInfo
  isSelected: boolean
  onSelect: (name: string | null) => void
}

export function BlueprintCard({ blueprint, isSelected, onSelect }: BlueprintCardProps) {
  return (
    <motion.button
      type="button"
      aria-pressed={isSelected}
      variants={cardEntrance}
      onClick={() => onSelect(isSelected ? null : blueprint.name)}
      className={cn(
        'flex flex-col items-start gap-2 rounded-lg border p-card text-left transition-colors',
        'hover:border-accent/50 hover:bg-card-hover',
        isSelected
          ? 'border-accent bg-accent/5'
          : 'border-border bg-card',
      )}
    >
      <div className="flex w-full items-center justify-between">
        <div className="flex items-center gap-2">
          <FileCode2 className="size-4 text-muted" />
          <span className="text-sm font-medium text-foreground">
            {blueprint.display_name}
          </span>
        </div>
        <span className="rounded-md bg-muted/20 px-1.5 py-0.5 text-xs text-muted">
          {formatLabel(blueprint.workflow_type)}
        </span>
      </div>

      <p className="line-clamp-2 text-xs text-muted">{blueprint.description}</p>

      <div className="flex gap-2">
        <StatPill label="Nodes" value={blueprint.node_count} />
        <StatPill label="Edges" value={blueprint.edge_count} />
      </div>
    </motion.button>
  )
}

export function BlueprintPicker({
  selectedBlueprint,
  onSelect,
  workflowTypeFilter,
}: BlueprintPickerProps) {
  const blueprints = useWorkflowsStore((s) => s.blueprints)
  const loading = useWorkflowsStore((s) => s.blueprintsLoading)
  const error = useWorkflowsStore((s) => s.blueprintsError)
  const loadBlueprints = useWorkflowsStore((s) => s.loadBlueprints)

  useEffect(() => {
    if (blueprints.length === 0 && !loading && !error) {
      void loadBlueprints()
    }
  }, [blueprints.length, loading, error, loadBlueprints])

  const filtered = workflowTypeFilter
    ? blueprints.filter((bp) => bp.workflow_type === workflowTypeFilter)
    : blueprints

  // Clear selection if the selected blueprint is no longer in the filtered list.
  useEffect(() => {
    if (
      selectedBlueprint &&
      filtered.length > 0 &&
      !filtered.some((bp) => bp.name === selectedBlueprint)
    ) {
      onSelect(null)
    }
  }, [filtered, selectedBlueprint, onSelect])

  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-grid-gap">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-lg" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger"
      >
        Failed to load blueprints: {error}
      </div>
    )
  }

  return (
    <motion.div
      className="grid grid-cols-2 gap-grid-gap"
      variants={staggerChildren}
      initial="hidden"
      animate="visible"
    >
      {filtered.map((bp) => (
        <BlueprintCard
          key={bp.name}
          blueprint={bp}
          isSelected={selectedBlueprint === bp.name}
          onSelect={onSelect}
        />
      ))}
    </motion.div>
  )
}
