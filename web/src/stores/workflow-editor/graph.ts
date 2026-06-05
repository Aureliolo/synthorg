import type { StoreApi } from 'zustand'
import {
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type Node,
  type EdgeChange,
  type NodeChange,
} from '@xyflow/react'
import {
  generateEdgeId,
  generateNodeId,
  nodeTypeToEdgeType,
  regenerateYaml,
} from './yaml'
import {
  MAX_UNDO,
  type GraphSlice,
  type SliceCreator,
  type WorkflowEditorState,
  type WorkflowSnapshot,
} from './types'

type WfSet = StoreApi<WorkflowEditorState>['setState']
type WfGet = StoreApi<WorkflowEditorState>['getState']

function deriveEdgeData(
  sourceNodeType: string | undefined,
  sourceHandle: string | null | undefined,
): Record<string, unknown> {
  const isTrueBranch = sourceHandle === 'true'
  if (sourceNodeType === 'conditional') {
    return {
      edgeType: isTrueBranch ? 'conditional_true' : 'conditional_false',
      branch: isTrueBranch ? 'true' : 'false',
    }
  }
  if (sourceNodeType === 'parallel_split') {
    return { edgeType: 'parallel_branch', branch: undefined }
  }
  return { edgeType: 'sequential', branch: undefined }
}

function snapshotOf(get: WfGet): WorkflowSnapshot {
  const { nodes, edges } = get()
  return {
    nodes: structuredClone(nodes),
    edges: structuredClone(edges),
  }
}

function addNodeImpl(
  set: WfSet,
  get: WfGet,
  type: GraphSlice['nodes'][number]['type'],
  position: { x: number; y: number },
): void {
  const { nodes, edges, definition } = get()
  const snapshot = snapshotOf(get)
  const id = generateNodeId()
  const label = type
    ? type.charAt(0).toUpperCase() + type.slice(1).replaceAll('_', ' ')
    : 'Node'
  const newNode: Node = { id, type, position, data: { label, config: {} } }
  const newNodes = [...nodes, newNode]
  const yaml = regenerateYaml(newNodes, edges, definition)
  set((s) => ({
    nodes: newNodes,
    dirty: true,
    undoStack: [...s.undoStack.slice(-MAX_UNDO + 1), snapshot],
    redoStack: [],
    yamlPreview: yaml,
  }))
}

function updateNodeConfigImpl(
  set: WfSet,
  get: WfGet,
  nodeId: string,
  config: Record<string, unknown>,
): void {
  const { nodes, edges, definition } = get()
  const snapshot = snapshotOf(get)
  const newNodes = nodes.map((n) =>
    n.id === nodeId
      ? { ...n, data: { ...(n.data), config } }
      : n,
  )
  const yaml = regenerateYaml(newNodes, edges, definition)
  set((s) => ({
    nodes: newNodes,
    dirty: true,
    undoStack: [...s.undoStack.slice(-MAX_UNDO + 1), snapshot],
    redoStack: [],
    yamlPreview: yaml,
  }))
}

function removeNodeImpl(set: WfSet, get: WfGet, nodeId: string): void {
  const { nodes, edges, definition } = get()
  const snapshot = snapshotOf(get)
  const newNodes = nodes.filter((n) => n.id !== nodeId)
  const newEdges = edges.filter(
    (e) => e.source !== nodeId && e.target !== nodeId,
  )
  const yaml = regenerateYaml(newNodes, newEdges, definition)
  set((s) => ({
    nodes: newNodes,
    edges: newEdges,
    dirty: true,
    selectedNodeId: s.selectedNodeId === nodeId ? null : s.selectedNodeId,
    undoStack: [...s.undoStack.slice(-MAX_UNDO + 1), snapshot],
    redoStack: [],
    yamlPreview: yaml,
  }))
}

function onConnectImpl(
  set: WfSet,
  get: WfGet,
  connection: Parameters<GraphSlice['onConnect']>[0],
): void {
  if (!connection.source || !connection.target) return
  const { nodes, edges, definition } = get()
  const snapshot = snapshotOf(get)
  const sourceNode = nodes.find((n) => n.id === connection.source)
  const newEdge: Edge = {
    id: generateEdgeId(),
    source: connection.source,
    target: connection.target,
    type: nodeTypeToEdgeType(sourceNode?.type),
    sourceHandle: connection.sourceHandle ?? undefined,
    targetHandle: connection.targetHandle ?? undefined,
    data: deriveEdgeData(sourceNode?.type, connection.sourceHandle),
  }
  const newEdges = [...edges, newEdge]
  const yaml = regenerateYaml(nodes, newEdges, definition)
  set((s) => ({
    edges: newEdges,
    dirty: true,
    undoStack: [...s.undoStack.slice(-MAX_UNDO + 1), snapshot],
    redoStack: [],
    yamlPreview: yaml,
  }))
}

function removeEdgeImpl(set: WfSet, get: WfGet, edgeId: string): void {
  const { nodes, edges, definition } = get()
  const snapshot = snapshotOf(get)
  const newEdges = edges.filter((e) => e.id !== edgeId)
  const yaml = regenerateYaml(nodes, newEdges, definition)
  set((s) => ({
    edges: newEdges,
    dirty: true,
    undoStack: [...s.undoStack.slice(-MAX_UNDO + 1), snapshot],
    redoStack: [],
    yamlPreview: yaml,
  }))
}

function onNodesChangeImpl(set: WfSet, changes: NodeChange[]): void {
  set((s) => {
    const hasMoves = changes.some(
      (c) => c.type === 'position' || c.type === 'remove',
    )
    const snapshot = hasMoves
      ? {
          nodes: structuredClone(s.nodes),
          edges: structuredClone(s.edges),
        }
      : null
    const newNodes = applyNodeChanges(changes, s.nodes)
    return {
      nodes: newNodes,
      dirty: s.dirty || hasMoves,
      yamlPreview: hasMoves
        ? regenerateYaml(newNodes, s.edges, s.definition)
        : s.yamlPreview,
      undoStack: snapshot
        ? [...s.undoStack.slice(-MAX_UNDO + 1), snapshot]
        : s.undoStack,
      redoStack: snapshot ? [] : s.redoStack,
    }
  })
}

function onEdgesChangeImpl(set: WfSet, changes: EdgeChange[]): void {
  set((s) => {
    const hasRemoves = changes.some((c) => c.type === 'remove')
    const snapshot = hasRemoves
      ? {
          nodes: structuredClone(s.nodes),
          edges: structuredClone(s.edges),
        }
      : null
    const newEdges = applyEdgeChanges(changes, s.edges)
    return {
      edges: newEdges,
      dirty: s.dirty || hasRemoves,
      yamlPreview: hasRemoves
        ? regenerateYaml(s.nodes, newEdges, s.definition)
        : s.yamlPreview,
      undoStack: snapshot
        ? [...s.undoStack.slice(-MAX_UNDO + 1), snapshot]
        : s.undoStack,
      redoStack: snapshot ? [] : s.redoStack,
    }
  })
}

export const createGraphSlice: SliceCreator<GraphSlice> = (set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  dirty: false,
  yamlPreview: '',

  addNode: (type, position) => addNodeImpl(set, get, type, position),
  updateNodeConfig: (nodeId, config) =>
    updateNodeConfigImpl(set, get, nodeId, config),
  removeNode: (nodeId) => removeNodeImpl(set, get, nodeId),
  onConnect: (connection) => onConnectImpl(set, get, connection),
  removeEdge: (edgeId) => removeEdgeImpl(set, get, edgeId),
  onNodesChange: (changes) => onNodesChangeImpl(set, changes),
  onEdgesChange: (changes) => onEdgesChangeImpl(set, changes),
  selectNode: (nodeId) => {
    set({ selectedNodeId: nodeId })
  },
})
