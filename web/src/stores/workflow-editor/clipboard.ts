import { copyNodes, pasteFromClipboard } from '@/pages/workflow-editor/copy-paste'
import { generateYamlPreview } from '@/pages/workflow-editor/workflow-to-yaml'
import { MAX_UNDO, type ClipboardSlice, type SliceCreator } from './types'

export const createClipboardSlice: SliceCreator<ClipboardSlice> = (set, get) => ({
  clipboard: null,

  copySelectedNodes: () => {
    const { nodes, edges } = get()
    const selectedIds = new Set(nodes.filter((n) => n.selected).map((n) => n.id))
    const clipboard = copyNodes(selectedIds, nodes, edges)
    if (clipboard) set({ clipboard })
  },

  pasteNodes: () => {
    const { clipboard, nodes, edges, definition } = get()
    if (!clipboard) return
    const pasted = pasteFromClipboard(clipboard)
    const newNodes = [...nodes.map((n) => ({ ...n, selected: false })), ...pasted.nodes]
    const newEdges = [...edges, ...pasted.edges]
    const yamlPreview = definition
      ? generateYamlPreview(newNodes, newEdges, definition.name, definition.workflow_type)
      : ''
    set({
      nodes: newNodes,
      edges: newEdges,
      dirty: true,
      yamlPreview,
      undoStack: [...get().undoStack.slice(-(MAX_UNDO - 1)), { nodes, edges }],
      redoStack: [],
    })
  },
})
