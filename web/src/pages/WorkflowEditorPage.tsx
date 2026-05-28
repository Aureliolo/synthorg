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
    if (isValidViewport(parsed)) return parsed
  } catch (err) {
    log.warn('Failed to load viewport from localStorage:', err)
  }
  return undefined
}

function isValidViewport(value: unknown): value is { x: number; y: number; zoom: number } {
  if (typeof value !== 'object' || value === null) return false
  const rec = value as Record<string, unknown>
  return isFiniteNumber(rec.x) && isFiniteNumber(rec.y) && isFiniteNumber(rec.zoom) && rec.zoom > 0
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

interface SelectedNodeDetails {
  readonly node: Node
  readonly type: WorkflowNodeType | null
  readonly label: string
  readonly config: Record<string, unknown>
}

/** Resolve the currently-selected node and its display props in one place. */
function getSelectedNodeDetails(
  nodes: readonly Node[],
  selectedNodeId: string | null,
): SelectedNodeDetails | null {
  if (!selectedNodeId) return null
  const node = nodes.find((n) => n.id === selectedNodeId)
  if (!node) return null
  const data = (node.data ?? {}) as { label?: unknown; config?: unknown }
  return {
    node,
    type: resolveSupportedNodeType(node.type),
    label: typeof data.label === 'string' ? data.label : 'Node',
    config: extractConfigObject(data.config),
  }
}

function resolveSupportedNodeType(nodeType: string | undefined): WorkflowNodeType | null {
  if (typeof nodeType !== 'string') return null
  return SUPPORTED_NODE_TYPES.has(nodeType as WorkflowNodeType)
    ? (nodeType as WorkflowNodeType)
    : null
}

function extractConfigObject(config: unknown): Record<string, unknown> {
  if (config && typeof config === 'object') return config as Record<string, unknown>
  return {}
}

function WorkflowEditorInner() {
  const state = useWorkflowEditorState()
  const [editorMode, setEditorMode] = useState<'visual' | 'yaml'>('visual')
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
  useInitialDefinition(defId, state.loadDefinition, state.createDefinition)
  const lifecycle = deriveLifecycleMode(state)
  if (lifecycle === 'loading') return <WorkflowEditorSkeleton />
  if (lifecycle === 'error') {
    return (
      <EmptyState
        icon={Workflow}
        title="Failed to load workflow"
        description={state.error ?? undefined}
      />
    )
  }
  return (
    <WorkflowEditorReadyView
      state={state}
      callbacks={callbacks}
      defId={defId}
      editorMode={editorMode}
      onEditorModeChange={setEditorMode}
      defaultViewport={defaultViewport}
    />
  )
}

type WorkflowEditorLifecycle = 'loading' | 'error' | 'ready'

function deriveLifecycleMode(
  state: ReturnType<typeof useWorkflowEditorState>,
): WorkflowEditorLifecycle {
  if (state.loading) return 'loading'
  if (!state.definition && state.error) return 'error'
  return 'ready'
}

interface WorkflowEditorReadyViewProps {
  state: ReturnType<typeof useWorkflowEditorState>
  callbacks: ReturnType<typeof useWorkflowEditorCallbacks>
  defId: string | null
  editorMode: 'visual' | 'yaml'
  onEditorModeChange: (mode: 'visual' | 'yaml') => void
  defaultViewport: { x: number; y: number; zoom: number } | undefined
}

function WorkflowEditorReadyView({
  state,
  callbacks,
  defId,
  editorMode,
  onEditorModeChange,
  defaultViewport,
}: WorkflowEditorReadyViewProps) {
  const selectedNodeDetails = getSelectedNodeDetails(state.nodes, state.selectedNodeId)
  return (
    <div className="flex h-full flex-col">
      <WorkflowEditorErrorBanner error={state.error} />
      <div className="mb-2">
        <WorkflowEditorToolbarRow
          state={state}
          callbacks={callbacks}
          defId={defId}
          editorMode={editorMode}
          onEditorModeChange={onEditorModeChange}
        />
      </div>
      <EditorModeContent
        editorMode={editorMode}
        state={state}
        callbacks={callbacks}
        defaultViewport={defaultViewport}
      />
      <WorkflowEditorSidebarSlot
        state={state}
        callbacks={callbacks}
        editorMode={editorMode}
        selectedNodeDetails={selectedNodeDetails}
      />
    </div>
  )
}

interface WorkflowEditorErrorBannerProps {
  error: string | null
}

function WorkflowEditorErrorBanner({ error }: WorkflowEditorErrorBannerProps) {
  if (!error) return null
  return (
    <div className="mb-2">
      <ErrorBanner severity="error" title="Workflow editor error" description={error} />
    </div>
  )
}

interface WorkflowEditorSidebarSlotProps {
  state: ReturnType<typeof useWorkflowEditorState>
  callbacks: ReturnType<typeof useWorkflowEditorCallbacks>
  editorMode: 'visual' | 'yaml'
  selectedNodeDetails: SelectedNodeDetails | null
}

function WorkflowEditorSidebarSlot({
  state,
  callbacks,
  editorMode,
  selectedNodeDetails,
}: WorkflowEditorSidebarSlotProps) {
  const drawerOpen = computeNodeDrawerOpen(
    editorMode,
    selectedNodeDetails,
    state.versionHistoryOpen,
  )
  return (
    <WorkflowEditorSidebar
      nodeDrawerOpen={drawerOpen}
      onNodeDrawerClose={callbacks.handleDrawerClose}
      selectedNodeId={state.selectedNodeId}
      selectedNodeType={selectedNodeDetails?.type ?? null}
      selectedNodeLabel={selectedNodeDetails?.label ?? 'Node'}
      selectedNodeConfig={selectedNodeDetails?.config ?? {}}
      onConfigChange={callbacks.handleConfigChange}
      versionHistoryOpen={state.versionHistoryOpen}
      onVersionHistoryClose={state.toggleVersionHistory}
    />
  )
}

function computeNodeDrawerOpen(
  editorMode: 'visual' | 'yaml',
  selectedNodeDetails: SelectedNodeDetails | null,
  versionHistoryOpen: boolean,
): boolean {
  if (editorMode !== 'visual') return false
  if (selectedNodeDetails === null) return false
  return !versionHistoryOpen
}

interface WorkflowEditorToolbarRowProps {
  state: ReturnType<typeof useWorkflowEditorState>
  callbacks: ReturnType<typeof useWorkflowEditorCallbacks>
  defId: string | null
  editorMode: 'visual' | 'yaml'
  onEditorModeChange: (mode: 'visual' | 'yaml') => void
}

function WorkflowEditorToolbarRow({
  state,
  callbacks,
  defId,
  editorMode,
  onEditorModeChange,
}: WorkflowEditorToolbarRowProps) {
  return (
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
      onEditorModeChange={onEditorModeChange}
      canUndo={state.undoStack.length > 0}
      canRedo={state.redoStack.length > 0}
      dirty={state.dirty}
      saving={state.saving}
      validating={state.validating}
      validationValid={state.validationResult ? state.validationResult.valid : null}
    />
  )
}

function useInitialDefinition(
  defId: string | null,
  loadDefinition: ReturnType<typeof useWorkflowEditorState>['loadDefinition'],
  createDefinition: ReturnType<typeof useWorkflowEditorState>['createDefinition'],
): void {
  const createdInitialDraftRef = useRef(false)
  useEffect(() => {
    if (defId) {
      void loadDefinition(defId)
      return
    }
    // React 19 Strict Mode replays mount effects: this guard avoids POSTing
    // two empty draft workflows on the first visit to the editor.
    if (createdInitialDraftRef.current) return
    createdInitialDraftRef.current = true
    void createDefinition('New Workflow', 'sequential_pipeline')
  }, [defId, loadDefinition, createDefinition])
}

interface EditorModeContentProps {
  editorMode: 'visual' | 'yaml'
  state: ReturnType<typeof useWorkflowEditorState>
  callbacks: ReturnType<typeof useWorkflowEditorCallbacks>
  defaultViewport: { x: number; y: number; zoom: number } | undefined
}

function EditorModeContent({
  editorMode,
  state,
  callbacks,
  defaultViewport,
}: EditorModeContentProps) {
  if (editorMode === 'yaml') {
    return (
      <div className="min-h-0 flex-1 rounded-lg border border-border">
        <WorkflowYamlEditor initialYaml={state.yamlPreview} />
      </div>
    )
  }
  return (
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
  )
}

export default function WorkflowEditorPage() {
  // Responsive height: phones/tablets get extra vertical budget (mobile
  // browser chrome + on-screen keyboard eat ~9rem of viewport); desktop keeps
  // the original 7rem allowance so the bottom toolbar stays visible.
  return (
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
