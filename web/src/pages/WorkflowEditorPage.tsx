import { useEffect, useMemo, useRef, useState } from 'react'
import { ReactFlowProvider, type Node } from '@xyflow/react'
import { Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router'
import { createLogger } from '@/lib/logger'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import type { WorkflowNodeType } from '@/api/types/workflows'
import { AgentAssignmentNode } from './workflow-editor/AgentAssignmentNode'
import { ConditionalEdge } from './workflow-editor/ConditionalEdge'
import { ConditionalNode } from './workflow-editor/ConditionalNode'
import { EndNode } from './workflow-editor/EndNode'
import { ParallelJoinNode } from './workflow-editor/ParallelJoinNode'
import { ParallelSplitNode } from './workflow-editor/ParallelSplitNode'
import { SequentialEdge } from './workflow-editor/SequentialEdge'
import { StartNode } from './workflow-editor/StartNode'
import { SubworkflowNode } from './workflow-editor/SubworkflowNode'
import { TaskNode } from './workflow-editor/TaskNode'
import { WorkflowEditorCanvas } from './workflow-editor/WorkflowEditorCanvas'
import { WorkflowEditorSidebar } from './workflow-editor/WorkflowEditorSidebar'
import { WorkflowEditorSkeleton } from './workflow-editor/WorkflowEditorSkeleton'
import { WorkflowToolbar } from './workflow-editor/WorkflowToolbar'
import { WorkflowYamlEditor } from './workflow-editor/WorkflowYamlEditor'
import { WorkflowYamlPreview } from './workflow-editor/WorkflowYamlPreview'
import { useWorkflowEditorCallbacks } from './workflow-editor/useWorkflowEditorCallbacks'
import { useWorkflowEditorKeyboard } from './workflow-editor/useWorkflowEditorKeyboard'
import { useWorkflowEditorState } from './workflow-editor/useWorkflowEditorState'

const log = createLogger('WorkflowEditor')

const nodeTypes = {
  start: StartNode,
  end: EndNode,
  task: TaskNode,
  agent_assignment: AgentAssignmentNode,
  conditional: ConditionalNode,
  parallel_split: ParallelSplitNode,
  parallel_join: ParallelJoinNode,
  subworkflow: SubworkflowNode,
}

const SUPPORTED_NODE_TYPES: ReadonlySet<WorkflowNodeType> = new Set(
  Object.keys(nodeTypes) as WorkflowNodeType[],
)

const edgeTypes = {
  sequential: SequentialEdge,
  conditional: ConditionalEdge,
}

const VIEWPORT_KEY = 'synthorg:workflow:viewport'

function saveViewport(viewport: { x: number; y: number; zoom: number }) {
  try {
    localStorage.setItem(VIEWPORT_KEY, JSON.stringify(viewport))
  } catch (err) {
    log.warn('Failed to save viewport to localStorage:', err)
  }
}

function loadViewport(): { x: number; y: number; zoom: number } | undefined {
  try {
    const stored = localStorage.getItem(VIEWPORT_KEY)
    if (!stored) return undefined
    const parsed: unknown = JSON.parse(stored)
    const rec = parsed as Record<string, unknown>
    if (
      typeof parsed === 'object' && parsed !== null &&
      typeof rec.x === 'number' && Number.isFinite(rec.x) &&
      typeof rec.y === 'number' && Number.isFinite(rec.y) &&
      typeof rec.zoom === 'number' && Number.isFinite(rec.zoom) && (rec.zoom as number) > 0
    ) {
      return parsed as { x: number; y: number; zoom: number }
    }
  } catch (err) {
    log.warn('Failed to load viewport from localStorage:', err)
  }
  return undefined
}

interface SelectedNodeDetails {
  readonly node: Node
  readonly type: WorkflowNodeType | null
  readonly label: string
  readonly config: Record<string, unknown>
}

/** Resolve the currently-selected node and its display props in one place,
 *  so the sidebar receives a validated shape rather than raw inline casts.
 *  `type` is validated against {@link SUPPORTED_NODE_TYPES}; unknown types
 *  resolve to `null` so the sidebar never renders a config UI for a type
 *  it cannot handle. */
function getSelectedNodeDetails(
  nodes: readonly Node[],
  selectedNodeId: string | null,
): SelectedNodeDetails | null {
  if (!selectedNodeId) return null
  const node = nodes.find((n) => n.id === selectedNodeId)
  if (!node) return null
  const data = (node.data ?? {}) as { label?: unknown; config?: unknown }
  const label = typeof data.label === 'string' ? data.label : 'Node'
  const config = (data.config && typeof data.config === 'object' ? data.config : {}) as Record<string, unknown>
  const type =
    typeof node.type === 'string' && SUPPORTED_NODE_TYPES.has(node.type as WorkflowNodeType)
      ? (node.type as WorkflowNodeType)
      : null
  return { node, type, label, config }
}

function WorkflowEditorInner() {
  const state = useWorkflowEditorState()
  const [editorMode, setEditorMode] = useState<'visual' | 'yaml'>('visual')
  const createdInitialDraftRef = useRef(false)
  const [searchParams] = useSearchParams()
  const defId = searchParams.get('id')

  const defaultViewport = useMemo(() => loadViewport(), [])

  useWorkflowEditorKeyboard(editorMode)

  const callbacks = useWorkflowEditorCallbacks({
    selectedNodeId: state.selectedNodeId,
    addNode: state.addNode,
    selectNode: state.selectNode,
    updateNodeConfig: state.updateNodeConfig,
    exportYaml: state.exportYaml,
    saveDefinition: state.saveDefinition,
    validate: state.validate,
    saveViewport,
  })

  const { loadDefinition, createDefinition } = state
  useEffect(() => {
    if (defId) {
      loadDefinition(defId)
      return
    }
    // React 19 Strict Mode replays mount effects -- without this guard we
    // would POST two empty draft workflows on the first visit to the editor.
    if (createdInitialDraftRef.current) return
    createdInitialDraftRef.current = true
    createDefinition('New Workflow', 'sequential_pipeline')
  }, [defId, loadDefinition, createDefinition])

  const selectedNodeDetails = getSelectedNodeDetails(state.nodes, state.selectedNodeId)

  if (state.loading) return <WorkflowEditorSkeleton />

  if (!state.loading && !state.definition && state.error) {
    return (
      <EmptyState
        icon={Workflow}
        title="Failed to load workflow"
        description={state.error}
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      {state.error && (
        <div className="mb-2">
          <ErrorBanner severity="error" title="Workflow editor error" description={state.error} />
        </div>
      )}

      <div className="mb-2">
        <WorkflowToolbar
          onAddNode={callbacks.handleAddNode}
          onUndo={state.undo}
          onRedo={state.redo}
          onSave={callbacks.handleSave}
          onValidate={callbacks.handleValidate}
          onExport={callbacks.handleExport}
          onHistory={state.toggleVersionHistory}
          onSaveAsNew={callbacks.handleSaveAsNew}
          onSwitchWorkflow={callbacks.handleSwitchWorkflow}
          currentWorkflowId={defId}
          editorMode={editorMode}
          onEditorModeChange={setEditorMode}
          canUndo={state.undoStack.length > 0}
          canRedo={state.redoStack.length > 0}
          dirty={state.dirty}
          saving={state.saving}
          validating={state.validating}
          validationValid={state.validationResult ? state.validationResult.valid : null}
        />
      </div>

      {editorMode === 'visual' ? (
        <>
          <WorkflowEditorCanvas
            nodes={state.nodes}
            edges={state.edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            defaultViewport={defaultViewport}
            onNodeClick={callbacks.handleNodeClick}
            onPaneClick={callbacks.handlePaneClick}
            onConnect={state.onConnect}
            onNodesChange={state.onNodesChange}
            onEdgesChange={state.onEdgesChange}
            onMoveEnd={callbacks.handleMoveEnd}
          />
          <WorkflowYamlPreview yaml={state.yamlPreview} />
        </>
      ) : (
        <div className="min-h-0 flex-1 rounded-lg border border-border">
          <WorkflowYamlEditor initialYaml={state.yamlPreview} />
        </div>
      )}

      <WorkflowEditorSidebar
        nodeDrawerOpen={editorMode === 'visual' && selectedNodeDetails !== null && !state.versionHistoryOpen}
        onNodeDrawerClose={callbacks.handleDrawerClose}
        selectedNodeId={state.selectedNodeId}
        selectedNodeType={selectedNodeDetails?.type ?? null}
        selectedNodeLabel={selectedNodeDetails?.label ?? 'Node'}
        selectedNodeConfig={selectedNodeDetails?.config ?? {}}
        onConfigChange={callbacks.handleConfigChange}
        versionHistoryOpen={state.versionHistoryOpen}
        onVersionHistoryClose={state.toggleVersionHistory}
      />
    </div>
  )
}

export default function WorkflowEditorPage() {
  return (
    // Responsive height: phones / tablets get extra vertical budget
    // (mobile browser chrome + on-screen keyboard eat ~9rem of viewport);
    // desktop keeps the original 7rem allowance so the bottom toolbar
    // stays visible inside the editor canvas.
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-section-gap md:h-[calc(100vh-7rem)]">
      <h1 className="text-lg font-semibold text-foreground">Workflow Editor</h1>

      <ErrorBoundary level="section">
        <ReactFlowProvider>
          <WorkflowEditorInner />
        </ReactFlowProvider>
      </ErrorBoundary>
    </div>
  )
}
