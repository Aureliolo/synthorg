/**
 * Entity definition card for the catalog grid.
 */
import { Menu } from '@base-ui/react/menu'
import { MoreHorizontal, Trash2 } from 'lucide-react'
import { memo, useState } from 'react'
import { cn } from '@/lib/utils'
import { ConfirmDialog, type ConfirmHandler } from '@/components/ui/confirm-dialog'
import type { EntityResponse } from '@/api/endpoints/ontology'

const TIER_STYLES = {
  core: 'bg-accent/10 text-accent border-accent/20',
  user: 'bg-success/10 text-success border-success/20',
} as const

const SOURCE_LABELS = {
  auto: 'Auto',
  config: 'Config',
  api: 'API',
} as const

const MENU_POPUP_CLASSES =
  'z-50 w-36 rounded-lg border border-border bg-card py-1 shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate,scale] duration-[var(--so-transition-fast)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95'

const MENU_ITEM_CLASSES =
  'flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm outline-none data-[highlighted]:bg-surface'

export interface EntityCardProps {
  entity: EntityResponse
  onClick?: () => void
  /**
   * When provided, renders a kebab menu with a guarded Delete action.
   * Returning ``false`` (the store's failure sentinel) keeps the
   * confirmation dialog open so the user can retry.
   */
  onDelete?: ConfirmHandler<[string]>
}

function EntityCardInner({ entity, onClick, onDelete }: EntityCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <>
      <div
        className={cn(
          'relative flex flex-col rounded-lg border border-border bg-card',
          'transition-colors hover:border-bright hover:bg-card-hover',
        )}
      >
        <button
          type="button"
          onClick={onClick}
          className={cn(
            'flex w-full flex-col gap-2 rounded-lg p-card text-left',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
          )}
          aria-label={`View entity: ${entity.name}`}
        >
          {/* Header */}
          <div className={cn('flex items-center justify-between', onDelete && 'pr-7')}>
            <h3 className="text-sm font-semibold text-foreground">
              {entity.name}
            </h3>
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  'rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase',
                  TIER_STYLES[entity.tier],
                )}
              >
                {entity.tier}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {SOURCE_LABELS[entity.source]}
              </span>
            </div>
          </div>

          {/* Definition */}
          {entity.definition && (
            <p className="line-clamp-2 text-xs text-text-secondary">
              {entity.definition}
            </p>
          )}

          {/* Meta row */}
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
            {entity.fields.length > 0 && (
              <span>{entity.fields.length} fields</span>
            )}
            {entity.relationships.length > 0 && (
              <span>{entity.relationships.length} relations</span>
            )}
            {entity.constraints.length > 0 && (
              <span>{entity.constraints.length} constraints</span>
            )}
          </div>
        </button>

        {onDelete && (
          <EntityCardMenu
            entityName={entity.name}
            onRequestDelete={() => setConfirmDelete(true)}
          />
        )}
      </div>

      {onDelete && (
        <ConfirmDialog
          open={confirmDelete}
          onOpenChange={setConfirmDelete}
          onConfirm={() => onDelete(entity.name)}
          title="Delete entity"
          description={`Are you sure you want to delete "${entity.name}"? This action cannot be undone.`}
          variant="destructive"
          confirmLabel="Delete"
        />
      )}
    </>
  )
}

export const EntityCard = memo(EntityCardInner)

interface EntityCardMenuProps {
  entityName: string
  onRequestDelete: () => void
}

function EntityCardMenu({ entityName, onRequestDelete }: EntityCardMenuProps) {
  return (
    <Menu.Root>
      <Menu.Trigger
        render={
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            className="absolute right-3 top-3 rounded p-1 text-muted-foreground hover:bg-surface hover:text-foreground"
            aria-label={`Entity actions: ${entityName}`}
          >
            <MoreHorizontal className="size-4" />
          </button>
        }
      />
      <Menu.Portal>
        <Menu.Positioner align="end" sideOffset={4}>
          <Menu.Popup className={MENU_POPUP_CLASSES}>
            <Menu.Item
              className={`${MENU_ITEM_CLASSES} text-danger`}
              onClick={onRequestDelete}
            >
              <Trash2 className="size-3.5" />
              Delete
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}
