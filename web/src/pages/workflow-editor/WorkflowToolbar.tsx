import { useCallback } from 'react'
import {
  ClipboardList,
  Clock,
  Copy,
  Download,
  GitBranch,
  Maximize,
  Merge,
  Redo2,
  Save,
  ShieldCheck,
  SplitSquareVertical,
  Undo2,
  UserCheck,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useReactFlow } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import type { WorkflowNodeType } from '@/api/types/workflows'
import { WorkflowSelector } from './WorkflowSelector'

interface NodePaletteItem {
  type: WorkflowNodeType
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const NODE_PALETTE: readonly NodePaletteItem[] = [
  { type: 'task', label: 'Task', icon: ClipboardList },
  { type: 'agent_assignment', label: 'Agent', icon: UserCheck },
  { type: 'conditional', label: 'Condition', icon: GitBranch },
  { type: 'parallel_split', label: 'Split', icon: SplitSquareVertical },
  { type: 'parallel_join', label: 'Join', icon: Merge },
]

export interface WorkflowToolbarProps {
  onAddNode: (type: WorkflowNodeType) => void
  onUndo: () => void
  onRedo: () => void
  onSave: () => void
  onValidate: () => void
  onExport: () => void
  onHistory?: () => void
  onSaveAsNew?: () => void
  onSwitchWorkflow?: (id: string) => void
  currentWorkflowId?: string | null
  editorMode?: 'visual' | 'yaml'
  onEditorModeChange?: (mode: 'visual' | 'yaml') => void
  canUndo: boolean
  canRedo: boolean
  dirty: boolean
  saving: boolean
  validating: boolean
  validationValid: boolean | null
}

export function WorkflowToolbar(props: WorkflowToolbarProps) {
  const { fitView, zoomIn, zoomOut } = useReactFlow()
  const handleFitView = useCallback(() => fitView({ padding: 0.2 }), [fitView])

  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-1 rounded-lg border border-border bg-surface px-2 py-1">
      {props.onSwitchWorkflow && (
        <div className="border-r border-border pr-2">
          <WorkflowSelector
            currentId={props.currentWorkflowId ?? null}
            onChange={props.onSwitchWorkflow}
          />
        </div>
      )}
      {props.onEditorModeChange && (
        <EditorModeToggle
          editorMode={props.editorMode ?? 'visual'}
          onEditorModeChange={props.onEditorModeChange}
        />
      )}
      <NodePaletteGroup onAddNode={props.onAddNode} />
      <UndoRedoGroup
        onUndo={props.onUndo}
        onRedo={props.onRedo}
        canUndo={props.canUndo}
        canRedo={props.canRedo}
      />
      <ZoomGroup
        onZoomIn={() => zoomIn()}
        onZoomOut={() => zoomOut()}
        onFitView={handleFitView}
      />
      <ActionGroup
        onValidate={props.onValidate}
        onExport={props.onExport}
        onHistory={props.onHistory}
        onSave={props.onSave}
        onSaveAsNew={props.onSaveAsNew}
        dirty={props.dirty}
        saving={props.saving}
        validating={props.validating}
        validationValid={props.validationValid}
      />
    </div>
  )
}

interface EditorModeToggleProps {
  editorMode: 'visual' | 'yaml'
  onEditorModeChange: (mode: 'visual' | 'yaml') => void
}

function EditorModeToggle({ editorMode, onEditorModeChange }: EditorModeToggleProps) {
  return (
    <div className="border-r border-border pr-2">
      <SegmentedControl
        label="Editor mode"
        value={editorMode}
        onChange={onEditorModeChange}
        options={[
          { value: 'visual' as const, label: 'Visual' },
          { value: 'yaml' as const, label: 'YAML' },
        ]}
        size="sm"
      />
    </div>
  )
}

interface NodePaletteGroupProps {
  onAddNode: WorkflowToolbarProps['onAddNode']
}

function NodePaletteGroup({ onAddNode }: NodePaletteGroupProps) {
  return (
    <div className="flex items-center gap-0.5 border-r border-border pr-2">
      {NODE_PALETTE.map(({ type, label, icon: Icon }) => (
        <Button
          key={type}
          variant="ghost"
          size="sm"
          title={`Add ${label}`}
          aria-label={`Add ${label} node`}
          onClick={() => onAddNode(type)}
          className="size-8 p-0"
        >
          <Icon className="size-4" aria-hidden="true" />
        </Button>
      ))}
    </div>
  )
}

interface UndoRedoGroupProps {
  onUndo: () => void
  onRedo: () => void
  canUndo: boolean
  canRedo: boolean
}

function UndoRedoGroup({ onUndo, onRedo, canUndo, canRedo }: UndoRedoGroupProps) {
  return (
    <div className="flex items-center gap-0.5 border-r border-border pr-2">
      <Button
        variant="ghost"
        size="sm"
        title="Undo"
        aria-label="Undo"
        onClick={onUndo}
        disabled={!canUndo}
        className="size-8 p-0"
      >
        <Undo2 className="size-4" aria-hidden="true" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        title="Redo"
        aria-label="Redo"
        onClick={onRedo}
        disabled={!canRedo}
        className="size-8 p-0"
      >
        <Redo2 className="size-4" aria-hidden="true" />
      </Button>
    </div>
  )
}

interface ZoomGroupProps {
  onZoomIn: () => void
  onZoomOut: () => void
  onFitView: () => void
}

function ZoomGroup({ onZoomIn, onZoomOut, onFitView }: ZoomGroupProps) {
  return (
    <div className="flex items-center gap-0.5 border-r border-border pr-2">
      <Button
        variant="ghost"
        size="sm"
        title="Zoom In"
        aria-label="Zoom in"
        onClick={onZoomIn}
        className="size-8 p-0"
      >
        <ZoomIn className="size-4" aria-hidden="true" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        title="Zoom Out"
        aria-label="Zoom out"
        onClick={onZoomOut}
        className="size-8 p-0"
      >
        <ZoomOut className="size-4" aria-hidden="true" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        title="Fit View"
        aria-label="Fit view"
        onClick={onFitView}
        className="size-8 p-0"
      >
        <Maximize className="size-4" aria-hidden="true" />
      </Button>
    </div>
  )
}

interface ActionGroupProps {
  onValidate: () => void
  onExport: () => void
  onHistory: WorkflowToolbarProps['onHistory']
  onSave: () => void
  onSaveAsNew: WorkflowToolbarProps['onSaveAsNew']
  dirty: boolean
  saving: boolean
  validating: boolean
  validationValid: boolean | null
}

function ActionGroup(props: ActionGroupProps) {
  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        title="Validate"
        aria-label="Validate workflow"
        onClick={props.onValidate}
        disabled={props.validating}
        className="gap-1.5"
      >
        <ShieldCheck className="size-4" aria-hidden="true" />
        <span className="text-xs">Validate</span>
        {props.validationValid !== null && (
          <span className={props.validationValid ? 'text-success' : 'text-danger'}>
            {props.validationValid ? '\u2713' : '\u2717'}
          </span>
        )}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        title="Export YAML"
        aria-label="Export as YAML"
        onClick={props.onExport}
        className="gap-1.5"
      >
        <Download className="size-4" aria-hidden="true" />
        <span className="text-xs">Export</span>
      </Button>
      {props.onHistory && (
        <Button
          variant="ghost"
          size="sm"
          title="Version History"
          aria-label="Version history"
          onClick={props.onHistory}
          className="gap-1.5"
        >
          <Clock className="size-4" aria-hidden="true" />
          <span className="text-xs">History</span>
        </Button>
      )}
      <Button
        variant="default"
        size="sm"
        title="Save"
        aria-label="Save workflow"
        onClick={props.onSave}
        disabled={!props.dirty || props.saving}
        className="gap-1.5"
      >
        <Save className="size-4" aria-hidden="true" />
        <span className="text-xs">{props.saving ? 'Saving...' : 'Save'}</span>
      </Button>
      {props.onSaveAsNew && (
        <Button
          variant="ghost"
          size="sm"
          title="Save as New"
          aria-label="Save as new workflow"
          onClick={props.onSaveAsNew}
          disabled={props.saving}
          className="gap-1.5"
        >
          <Copy className="size-4" aria-hidden="true" />
          <span className="text-xs">Save as New</span>
        </Button>
      )}
    </div>
  )
}
