import { Link, useNavigate } from 'react-router'
import { Menu } from '@base-ui/react/menu'
import { MoreHorizontal, Pencil, Copy, Download, Trash2 } from 'lucide-react'
import { memo, useState } from 'react'
import { ROUTES } from '@/router/routes'
import { StatPill } from '@/components/ui/stat-pill'
import { ConfirmDialog, type ConfirmHandler } from '@/components/ui/confirm-dialog'
import { formatRelativeTime, formatLabel, formatDateTime } from '@/utils/format'
import type { WorkflowDefinition } from '@/api/types/workflows'

interface WorkflowCardProps {
  workflow: WorkflowDefinition
  /** Returning ``false`` keeps the confirm dialog open so the user can retry. */
  onDelete: (id: string) => ReturnType<ConfirmHandler>
  onDuplicate: (id: string) => void
  /** Export the persisted definition as YAML. */
  onExport: (id: string) => void | Promise<void>
  /** When defined, renders a multi-select checkbox and marks the card as selectable. */
  onToggleSelect?: ((id: string) => void) | undefined
  /** Whether the row is currently included in a multi-select. */
  selected?: boolean | undefined
}

function WorkflowCardInner({
  workflow,
  onDelete,
  onDuplicate,
  onExport,
  onToggleSelect,
  selected = false,
}: WorkflowCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const navigate = useNavigate()
  const editorUrl = `${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(workflow.id)}`
  const cardClasses = `relative rounded-lg border bg-card p-card transition-all duration-[var(--so-transition-default)] hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)] ${
    selected ? 'border-accent ring-2 ring-accent/30' : 'border-border'
  }`

  return (
    <>
      <div className={cardClasses}>
        {onToggleSelect && (
          <SelectCheckbox
            workflowId={workflow.id}
            workflowName={workflow.name}
            selected={selected}
            onToggleSelect={onToggleSelect}
          />
        )}
        <Link to={editorUrl} className={`block ${onToggleSelect ? 'pl-7' : ''}`}>
          <WorkflowCardBody workflow={workflow} />
        </Link>
        <WorkflowCardMenu
          workflowName={workflow.name}
          editorUrl={editorUrl}
          onNavigate={navigate}
          onDuplicate={() => onDuplicate(workflow.id)}
          onExport={() => void onExport(workflow.id)}
          onRequestDelete={() => setConfirmDelete(true)}
        />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        onConfirm={() => onDelete(workflow.id)}
        title="Delete workflow"
        description={`Are you sure you want to delete "${workflow.name}"? This action cannot be undone.`}
        variant="destructive"
        confirmLabel="Delete"
      />
    </>
  )
}

export const WorkflowCard = memo(WorkflowCardInner)

interface SelectCheckboxProps {
  workflowId: string
  workflowName: string
  selected: boolean
  onToggleSelect: (id: string) => void
}

function SelectCheckbox({
  workflowId,
  workflowName,
  selected,
  onToggleSelect,
}: SelectCheckboxProps) {
  return (
    <label className="absolute left-3 top-3 z-10 flex cursor-pointer items-center">
      <input
        type="checkbox"
        className="size-4 rounded border-border accent-accent"
        checked={selected}
        onChange={() => onToggleSelect(workflowId)}
        onClick={(e) => e.stopPropagation()}
        aria-label={`Select workflow ${workflowName}`}
      />
    </label>
  )
}

interface WorkflowCardBodyProps {
  workflow: WorkflowDefinition
}

function WorkflowCardBody({ workflow }: WorkflowCardBodyProps) {
  return (
    <>
      <div className="mb-2 flex items-center gap-2">
        <span className="truncate text-sm font-semibold text-foreground">
          {workflow.name}
        </span>
        {/* Workflow type rendered through StatPill (consistent with the StatPill
            instances below for Nodes/Edges and with ArtifactCard's type label)
            instead of a hand-rolled inline pill that drifts from the design
            tokens. */}
        <StatPill value={formatLabel(workflow.workflow_type)} />
      </div>
      {workflow.description && (
        <p className="mb-3 line-clamp-2 text-xs text-muted-foreground">
          {workflow.description}
        </p>
      )}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <StatPill label="Nodes" value={workflow.nodes.length} />
        <StatPill label="Edges" value={workflow.edges.length} />
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>v{workflow.version}</span>
        <span>
          Updated{' '}
          <time dateTime={workflow.updated_at} title={formatDateTime(workflow.updated_at)}>
            {formatRelativeTime(workflow.updated_at)}
          </time>
        </span>
      </div>
      {workflow.is_subworkflow && (
        <div className="text-xs text-accent">Subworkflow</div>
      )}
    </>
  )
}

const MENU_POPUP_CLASSES =
  'z-50 w-36 rounded-lg border border-border bg-card py-1 shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate,scale] duration-[var(--so-transition-fast)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95'

const MENU_ITEM_CLASSES =
  'flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm outline-none data-[highlighted]:bg-surface'

interface WorkflowCardMenuProps {
  workflowName: string
  editorUrl: string
  onNavigate: (url: string) => unknown
  onDuplicate: () => void
  onExport: () => void
  onRequestDelete: () => void
}

function WorkflowCardMenu({
  workflowName,
  editorUrl,
  onNavigate,
  onDuplicate,
  onExport,
  onRequestDelete,
}: WorkflowCardMenuProps) {
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
            aria-label={`Workflow actions for ${workflowName}`}
          >
            <MoreHorizontal className="size-4" />
          </button>
        }
      />
      <Menu.Portal>
        <Menu.Positioner align="end" sideOffset={4}>
          <Menu.Popup className={MENU_POPUP_CLASSES}>
            <Menu.Item
              className={`${MENU_ITEM_CLASSES} text-foreground`}
              onClick={() => {
                void onNavigate(editorUrl)
              }}
            >
              <Pencil className="size-3.5" />
              Edit
            </Menu.Item>
            <Menu.Item
              className={`${MENU_ITEM_CLASSES} text-foreground`}
              onClick={onDuplicate}
            >
              <Copy className="size-3.5" />
              Duplicate
            </Menu.Item>
            <Menu.Item
              className={`${MENU_ITEM_CLASSES} text-foreground`}
              onClick={onExport}
            >
              <Download className="size-3.5" />
              Export YAML
            </Menu.Item>
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
