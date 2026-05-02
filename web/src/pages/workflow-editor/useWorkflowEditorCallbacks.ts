import { useCallback } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { useNavigate } from 'react-router'
import type { Node } from '@xyflow/react'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { useToastStore } from '@/stores/toast'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { useWorkflowsStore } from '@/stores/workflows'
import type { WorkflowNodeType } from '@/api/types/workflows'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
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
  saveDefinition: () => Promise<void>
  validate: () => Promise<void>
  saveViewport: (viewport: { x: number; y: number; zoom: number }) => void
}

export function useWorkflowEditorCallbacks(
  args: UseWorkflowEditorCallbacksArgs,
): WorkflowEditorCallbacks {
  const {
    selectedNodeId,
    addNode,
    selectNode,
    updateNodeConfig,
    exportYaml,
    saveDefinition,
    validate,
    saveViewport,
  } = args

  const addToast = useToastStore((s) => s.add)
  const navigate = useNavigate()

  const handleAddNode = useCallback(
    (type: WorkflowNodeType) => {
      addNode(type, { x: 250 + Math.random() * 100, y: 150 + Math.random() * 200 })
    },
    [addNode],
  )

  const handleNodeClick = useCallback(
    (_event: ReactMouseEvent, node: Node) => {
      selectNode(node.id)
    },
    [selectNode],
  )

  const handlePaneClick = useCallback(() => {
    selectNode(null)
  }, [selectNode])

  const handleExport = useCallback(async () => {
    try {
      const yamlStr = await exportYaml()
      const blob = new Blob([yamlStr], { type: 'text/yaml' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${useWorkflowEditorStore.getState().definition?.name ?? 'workflow'}.yaml`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      addToast({ variant: 'success', title: 'YAML exported' })
    } catch (err) {
      log.error('YAML export failed', sanitizeForLog(err))
      addToast({ variant: 'error', title: 'Export failed', description: getErrorMessage(err) })
    }
  }, [exportYaml, addToast])

  const handleSave = useCallback(async () => {
    await saveDefinition()
    const storeError = useWorkflowEditorStore.getState().error
    if (!storeError) {
      addToast({ variant: 'success', title: 'Workflow saved' })
    }
  }, [saveDefinition, addToast])

  const handleValidate = useCallback(async () => {
    await validate()
    const result = useWorkflowEditorStore.getState().validationResult
    if (result) {
      const errorCount = result.errors.length
      const errorWord = errorCount === 1 ? 'error' : 'errors'
      addToast({
        variant: result.valid ? 'success' : 'warning',
        title: result.valid
          ? 'Workflow is valid'
          : `Validation found ${errorCount} ${errorWord}`,
        description: result.valid
          ? undefined
          : 'Review the marked fields below.',
      })
    }
  }, [validate, addToast])

  const handleDrawerClose = useCallback(() => selectNode(null), [selectNode])

  const handleConfigChange = useCallback(
    (config: Record<string, unknown>) => {
      if (selectedNodeId) updateNodeConfig(selectedNodeId, config)
    },
    [selectedNodeId, updateNodeConfig],
  )

  const handleSwitchWorkflow = useCallback(
    (id: string) => {
      navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(id)}`)
    },
    [navigate],
  )

  const handleSaveAsNew = useCallback(async () => {
    const state = useWorkflowEditorStore.getState()
    if (!state.definition) return
    // Per-field guards for n.data / e.data: ReactFlow types ``data`` as
    // ``Record<string, unknown> | undefined`` so each property is
    // ``unknown`` until validated. Without these the previous double
    // ``as`` casts ("as Record<string, unknown>" then "as string")
    // silently widened typos and missing fields into runtime crashes.
    const nodeData = state.nodes.map((n) => ({
      id: n.id,
      type: readString(n.data, 'nodeType') ?? n.type ?? 'task',
      label: readString(n.data, 'label') ?? n.id,
      position_x: n.position.x,
      position_y: n.position.y,
      config: readRecord(n.data, 'config') ?? {},
    }))
    const edgeData = state.edges.map((e) => ({
      id: e.id,
      source_node_id: e.source,
      target_node_id: e.target,
      type: readString(e.data, 'edgeType') ?? 'sequential',
      // ReactFlow edges may store the label on ``data.label`` (our
      // own write path) OR directly on ``e.label`` (older edges
      // hydrated from the wire format). Fall back to ``e.label`` so
      // duplicating a workflow preserves edge labels regardless of
      // where they currently live.
      label:
        readString(e.data, 'label')
        ?? (typeof e.label === 'string' ? e.label : null),
    }))
    const created = await useWorkflowsStore.getState().createWorkflow({
      name: `${state.definition.name} (Copy)`,
      description: state.definition.description || undefined,
      workflow_type: state.definition.workflow_type,
      nodes: nodeData,
      edges: edgeData,
    })
    if (!created) return
    navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(created.id)}`)
  }, [navigate])

  const handleMoveEnd = useCallback(
    (_event: unknown, viewport: { x: number; y: number; zoom: number }) => {
      saveViewport(viewport)
    },
    [saveViewport],
  )

  return {
    handleAddNode,
    handleNodeClick,
    handlePaneClick,
    handleExport,
    handleSave,
    handleValidate,
    handleDrawerClose,
    handleConfigChange,
    handleSwitchWorkflow,
    handleSaveAsNew,
    handleMoveEnd,
  }
}
