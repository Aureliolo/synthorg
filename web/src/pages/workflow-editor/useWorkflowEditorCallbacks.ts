import { useCallback } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { useNavigate } from 'react-router'
import type { Edge, Node } from '@xyflow/react'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { useToastStore } from '@/stores/toast'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { useWorkflowsStore } from '@/stores/workflows'
import { isWorkflowEdgeType, isWorkflowNodeType } from '@/api/types/workflows'
import type {
  WorkflowEdgeType,
  WorkflowIODeclaration,
  WorkflowIODeclarationRequest,
  WorkflowNodeType,
} from '@/api/types/workflows'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { downloadTextFile } from '@/utils/download'
import { isObject, isString } from '@/utils/type-guards'

const log = createLogger('WorkflowEditor')

/** Read ``data?.[key]`` and narrow it to ``string | undefined`` without ``as`` casts. */
function readString(data: unknown, key: string): string | undefined {
  if (!isObject(data)) return undefined
  const value = data[key]
  return isString(value) ? value : undefined
}

/** Read ``data?.[key]`` and narrow it to ``Record<string, unknown> | undefined``. */
function readRecord(data: unknown, key: string): Record<string, unknown> | undefined {
  if (!isObject(data)) return undefined
  const value = data[key]
  return isObject(value) ? value : undefined
}

export interface WorkflowEditorCallbacks {
  handleAddNode: (type: WorkflowNodeType) => void
  handleNodeClick: (event: ReactMouseEvent, node: Node) => void
  handlePaneClick: () => void
  handleExport: () => Promise<void>
  handleSave: () => Promise<void>
  handleValidate: () => Promise<void>
  handleDrawerClose: () => void
  handleConfigChange: (config: Record<string, unknown>) => void
  handleSwitchWorkflow: (id: string) => void
  handleSaveAsNew: () => Promise<void>
  handleMoveEnd: (event: unknown, viewport: { x: number; y: number; zoom: number }) => void
}

interface UseWorkflowEditorCallbacksArgs {
  selectedNodeId: string | null
  addNode: (type: WorkflowNodeType, pos: { x: number; y: number }) => void
  selectNode: (id: string | null) => void
  updateNodeConfig: (id: string, config: Record<string, unknown>) => void
  exportYaml: () => Promise<string>
  saveDefinition: () => Promise<boolean>
  validate: () => Promise<void>
  saveViewport: (viewport: { x: number; y: number; zoom: number }) => void
}

export function useWorkflowEditorCallbacks(
  args: UseWorkflowEditorCallbacksArgs,
): WorkflowEditorCallbacks {
  const addToast = useToastStore((s) => s.add)
  const navigate = useNavigate()
  const nodeOps = useNodeOpsCallbacks(args)
  const saveOps = useSaveOpsCallbacks(args, addToast)
  const handleSwitchWorkflow = useCallback(
    (id: string) => {
      void navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(id)}`)
    },
    [navigate],
  )
  const handleSaveAsNew = useCallback(() => duplicateWorkflow(navigate), [navigate])
  const { saveViewport } = args
  const handleMoveEnd = useCallback(
    (_event: unknown, viewport: { x: number; y: number; zoom: number }) => {
      saveViewport(viewport)
    },
    [saveViewport],
  )
  return {
    ...nodeOps,
    ...saveOps,
    handleSwitchWorkflow,
    handleSaveAsNew,
    handleMoveEnd,
  }
}

interface NodeOps {
  handleAddNode: WorkflowEditorCallbacks['handleAddNode']
  handleNodeClick: WorkflowEditorCallbacks['handleNodeClick']
  handlePaneClick: WorkflowEditorCallbacks['handlePaneClick']
  handleDrawerClose: WorkflowEditorCallbacks['handleDrawerClose']
  handleConfigChange: WorkflowEditorCallbacks['handleConfigChange']
}

function useNodeOpsCallbacks(args: UseWorkflowEditorCallbacksArgs): NodeOps {
  const { selectedNodeId, addNode, selectNode, updateNodeConfig } = args
  const handleAddNode = useCallback(
    (type: WorkflowNodeType) =>
      addNode(type, { x: 250 + Math.random() * 100, y: 150 + Math.random() * 200 }),
    [addNode],
  )
  const handleNodeClick = useCallback(
    (_event: ReactMouseEvent, node: Node) => selectNode(node.id),
    [selectNode],
  )
  const handlePaneClick = useCallback(() => selectNode(null), [selectNode])
  const handleDrawerClose = useCallback(() => selectNode(null), [selectNode])
  const handleConfigChange = useCallback(
    (config: Record<string, unknown>) => {
      if (selectedNodeId) updateNodeConfig(selectedNodeId, config)
    },
    [selectedNodeId, updateNodeConfig],
  )
  return {
    handleAddNode,
    handleNodeClick,
    handlePaneClick,
    handleDrawerClose,
    handleConfigChange,
  }
}

interface SaveOps {
  handleExport: WorkflowEditorCallbacks['handleExport']
  handleSave: WorkflowEditorCallbacks['handleSave']
  handleValidate: WorkflowEditorCallbacks['handleValidate']
}

function useSaveOpsCallbacks(
  args: UseWorkflowEditorCallbacksArgs,
  addToast: ReturnType<typeof useToastStore.getState>['add'],
): SaveOps {
  const { exportYaml, saveDefinition, validate } = args
  const handleExport = useCallback(async () => {
    try {
      const yamlStr = await exportYaml()
      const name = useWorkflowEditorStore.getState().definition?.name ?? 'workflow'
      downloadTextFile(yamlStr, `${name}.yaml`, 'text/yaml')
      addToast({ variant: 'success', title: 'YAML exported' })
    } catch (err) {
      log.error('YAML export failed', sanitizeForLog(err))
      addToast({
        variant: 'error',
        title: 'Export failed',
        description: getErrorMessage(err),
      })
    }
  }, [exportYaml, addToast])
  const handleSave = useCallback(async () => {
    await saveDefinition()
  }, [saveDefinition])
  const handleValidate = useCallback(async () => {
    await validate()
    emitValidationToast(addToast)
  }, [validate, addToast])
  return { handleExport, handleSave, handleValidate }
}

function emitValidationToast(
  addToast: ReturnType<typeof useToastStore.getState>['add'],
): void {
  const { validationResult: result, error } = useWorkflowEditorStore.getState()
  // ``validate`` clears ``validationResult`` and sets ``error`` on every
  // failure path (no workflow loaded, malformed graph, API error). Without
  // surfacing ``error`` here the click produced no feedback at all.
  if (!result) {
    if (error) {
      addToast({ variant: 'error', title: 'Validation failed', description: error })
    }
    return
  }
  const errorCount = result.errors.length
  const errorWord = errorCount === 1 ? 'error' : 'errors'
  addToast({
    variant: result.valid ? 'success' : 'warning',
    title: result.valid
      ? 'Workflow is valid'
      : `Validation found ${errorCount} ${errorWord}`,
    description: result.valid ? undefined : 'Review the marked fields below.',
  })
}

async function duplicateWorkflow(navigate: ReturnType<typeof useNavigate>): Promise<void> {
  const state = useWorkflowEditorStore.getState()
  if (!state.definition) return
  const nodeData = state.nodes.map(extractNodePayload)
  const edgeData = state.edges.map(extractEdgePayload)
  const created = await useWorkflowsStore.getState().createWorkflow({
    name: `${state.definition.name} (Copy)`,
    description: state.definition.description,
    version: '1.0.0',
    workflow_type: state.definition.workflow_type,
    inputs: state.definition.inputs.map(toIORequest),
    outputs: state.definition.outputs.map(toIORequest),
    is_subworkflow: false,
    nodes: nodeData,
    edges: edgeData,
  })
  if (!created) return
  void navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(created.id)}`)
}

function extractNodePayload(n: Node): {
  id: string
  type: WorkflowNodeType
  label: string
  position_x: number
  position_y: number
  config: Record<string, unknown>
} {
  // Per-field guards for n.data: ReactFlow types ``data`` as
  // ``Record<string, unknown> | undefined`` so each property is ``unknown``
  // until validated. Without these the previous double-cast silently widened
  // typos and missing fields into runtime crashes.
  const dataType = readString(n.data, 'nodeType')
  const reactFlowType = n.type
  const nodeType: WorkflowNodeType = isWorkflowNodeType(dataType)
    ? dataType
    : isWorkflowNodeType(reactFlowType)
      ? reactFlowType
      : 'task'
  return {
    id: n.id,
    type: nodeType,
    label: readString(n.data, 'label') ?? n.id,
    position_x: n.position.x,
    position_y: n.position.y,
    config: readRecord(n.data, 'config') ?? {},
  }
}

function extractEdgePayload(e: Edge): {
  id: string
  source_node_id: string
  target_node_id: string
  type: WorkflowEdgeType
  label: string | null
} {
  const dataType = readString(e.data, 'edgeType')
  const edgeType: WorkflowEdgeType = isWorkflowEdgeType(dataType) ? dataType : 'sequential'
  // ReactFlow edges may store the label on ``data.label`` (our write path) OR
  // directly on ``e.label`` (older edges hydrated from the wire format). Fall
  // back to ``e.label`` so duplicating a workflow preserves edge labels.
  const label =
    readString(e.data, 'label') ?? (typeof e.label === 'string' ? e.label : null)
  return {
    id: e.id,
    source_node_id: e.source,
    target_node_id: e.target,
    type: edgeType,
    label,
  }
}

// ``WorkflowIODeclarationRequest`` requires ``default`` (no ``?``) while
// ``WorkflowIODeclaration`` (source shape on the loaded definition) marks it
// optional. Normalise missing defaults to ``null`` so the duplicated workflow
// preserves the source's I/O shape.
function toIORequest(decl: WorkflowIODeclaration): WorkflowIODeclarationRequest {
  return {
    default: decl.default ?? null,
    description: decl.description,
    name: decl.name,
    required: decl.required,
    type: decl.type,
  }
}
