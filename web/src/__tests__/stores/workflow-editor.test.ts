import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import type { WorkflowDiff } from '@/api/types/workflows'
import { apiError } from '@/mocks/handlers'
import { buildWorkflow } from '@/mocks/handlers/workflows'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

function resetStore() {
  useWorkflowEditorStore.getState().reset()
}

describe('workflow-editor composed store', () => {
  beforeEach(() => {
    resetStore()
  })

  describe('composition', () => {
    it('exposes graph slice actions', () => {
      const state = useWorkflowEditorStore.getState()
      expect(typeof state.addNode).toBe('function')
      expect(typeof state.removeNode).toBe('function')
      expect(typeof state.onConnect).toBe('function')
      expect(typeof state.selectNode).toBe('function')
    })

    it('exposes undo/redo slice actions', () => {
      const state = useWorkflowEditorStore.getState()
      expect(typeof state.undo).toBe('function')
      expect(typeof state.redo).toBe('function')
    })

    it('exposes validation, clipboard, persistence, versions slices', () => {
      const state = useWorkflowEditorStore.getState()
      expect(typeof state.validate).toBe('function')
      expect(typeof state.copySelectedNodes).toBe('function')
      expect(typeof state.pasteNodes).toBe('function')
      expect(typeof state.loadDefinition).toBe('function')
      expect(typeof state.saveDefinition).toBe('function')
      expect(typeof state.toggleVersionHistory).toBe('function')
      expect(typeof state.rollback).toBe('function')
    })

    it('initializes with empty graph and clean flags', () => {
      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toEqual([])
      expect(state.edges).toEqual([])
      expect(state.selectedNodeId).toBeNull()
      expect(state.dirty).toBe(false)
      expect(state.undoStack).toEqual([])
      expect(state.redoStack).toEqual([])
      expect(state.validationResult).toBeNull()
      expect(state.clipboard).toBeNull()
      expect(state.definition).toBeNull()
    })
  })

  describe('graph + undo-redo integration', () => {
    it('adds a node, marks dirty, and records an undo snapshot', () => {
      useWorkflowEditorStore.getState().addNode('task', { x: 10, y: 20 })

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(1)
      expect(state.nodes[0]?.type).toBe('task')
      expect(state.nodes[0]?.position).toEqual({ x: 10, y: 20 })
      expect(state.dirty).toBe(true)
      expect(state.undoStack).toHaveLength(1)
      expect(state.redoStack).toEqual([])
    })

    it('undo restores prior state and pushes onto redo stack', () => {
      const store = useWorkflowEditorStore.getState()
      store.addNode('task', { x: 0, y: 0 })
      store.addNode('agent_assignment', { x: 100, y: 0 })

      expect(useWorkflowEditorStore.getState().nodes).toHaveLength(2)

      useWorkflowEditorStore.getState().undo()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(1)
      expect(state.redoStack).toHaveLength(1)
      expect(state.undoStack).toHaveLength(1)
    })

    it('redo re-applies the undone action', () => {
      const store = useWorkflowEditorStore.getState()
      store.addNode('task', { x: 0, y: 0 })
      store.addNode('agent_assignment', { x: 100, y: 0 })
      useWorkflowEditorStore.getState().undo()
      expect(useWorkflowEditorStore.getState().nodes).toHaveLength(1)

      useWorkflowEditorStore.getState().redo()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(2)
      expect(state.redoStack).toEqual([])
    })

    it('undo on an empty stack is a no-op', () => {
      const snapshotBefore = useWorkflowEditorStore.getState()
      expect(snapshotBefore.undoStack).toEqual([])
      expect(snapshotBefore.nodes).toEqual([])

      useWorkflowEditorStore.getState().undo()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toEqual([])
      expect(state.redoStack).toEqual([])
      expect(state.undoStack).toEqual([])
    })

    it('redo on an empty stack is a no-op', () => {
      useWorkflowEditorStore.getState().addNode('task', { x: 0, y: 0 })
      const snapshotBefore = useWorkflowEditorStore.getState()
      expect(snapshotBefore.redoStack).toEqual([])

      useWorkflowEditorStore.getState().redo()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(1)
      expect(state.redoStack).toEqual([])
    })

    it('a new action after undo clears the redo stack', () => {
      const store = useWorkflowEditorStore.getState()
      store.addNode('task', { x: 0, y: 0 })
      store.addNode('agent_assignment', { x: 50, y: 0 })
      useWorkflowEditorStore.getState().undo()
      expect(useWorkflowEditorStore.getState().redoStack).toHaveLength(1)

      useWorkflowEditorStore.getState().addNode('task', { x: 200, y: 200 })

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(2)
      expect(state.redoStack).toEqual([])
    })

    it('removeNode clears selectedNodeId when the removed node was selected', () => {
      const store = useWorkflowEditorStore.getState()
      store.addNode('task', { x: 0, y: 0 })
      const nodeId = useWorkflowEditorStore.getState().nodes[0]!.id
      store.selectNode(nodeId)
      expect(useWorkflowEditorStore.getState().selectedNodeId).toBe(nodeId)

      useWorkflowEditorStore.getState().removeNode(nodeId)

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toHaveLength(0)
      expect(state.selectedNodeId).toBeNull()
    })
  })

  describe('clipboard slice', () => {
    it('pasteNodes is a no-op when clipboard is empty', () => {
      expect(useWorkflowEditorStore.getState().clipboard).toBeNull()

      useWorkflowEditorStore.getState().pasteNodes()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toEqual([])
      expect(state.undoStack).toEqual([])
      expect(state.dirty).toBe(false)
    })
  })

  describe('versions slice', () => {
    it('toggleVersionHistory flips the open flag', () => {
      expect(useWorkflowEditorStore.getState().versionHistoryOpen).toBe(false)

      useWorkflowEditorStore.getState().toggleVersionHistory()
      expect(useWorkflowEditorStore.getState().versionHistoryOpen).toBe(true)

      useWorkflowEditorStore.getState().toggleVersionHistory()
      expect(useWorkflowEditorStore.getState().versionHistoryOpen).toBe(false)
    })

    it('clearDiff resets diffResult without other state', () => {
      const diffResult: WorkflowDiff = {
        definition_id: 'test-def',
        from_version: 1,
        to_version: 2,
        node_changes: [],
        edge_changes: [],
        metadata_changes: [],
        summary: 'A test diff',
      }
      useWorkflowEditorStore.setState({
        diffResult,
        diffLoading: false,
      })

      useWorkflowEditorStore.getState().clearDiff()

      expect(useWorkflowEditorStore.getState().diffResult).toBeNull()
    })
  })

  describe('persistence slice reset', () => {
    it('reset returns the store to its initial empty state', () => {
      const store = useWorkflowEditorStore.getState()
      store.addNode('task', { x: 5, y: 5 })
      expect(useWorkflowEditorStore.getState().nodes).toHaveLength(1)
      expect(useWorkflowEditorStore.getState().dirty).toBe(true)

      useWorkflowEditorStore.getState().reset()

      const state = useWorkflowEditorStore.getState()
      expect(state.nodes).toEqual([])
      expect(state.edges).toEqual([])
      expect(state.definition).toBeNull()
      expect(state.dirty).toBe(false)
      expect(state.undoStack).toEqual([])
      expect(state.redoStack).toEqual([])
    })

    it('reset also clears versions-slice state', () => {
      const diffResult: WorkflowDiff = {
        definition_id: 'test-def',
        from_version: 1,
        to_version: 2,
        node_changes: [],
        edge_changes: [],
        metadata_changes: [],
        summary: 'reset coverage',
      }
      useWorkflowEditorStore.setState({
        versionHistoryOpen: true,
        diffResult,
        diffLoading: true,
        versionsHasMore: true,
      })
      expect(useWorkflowEditorStore.getState().versionHistoryOpen).toBe(true)
      expect(useWorkflowEditorStore.getState().diffResult).not.toBeNull()

      useWorkflowEditorStore.getState().reset()

      const state = useWorkflowEditorStore.getState()
      expect(state.versionHistoryOpen).toBe(false)
      expect(state.diffResult).toBeNull()
      expect(state.diffLoading).toBe(false)
      expect(state.versions).toEqual([])
      expect(state.versionsLoading).toBe(false)
      expect(state.versionsHasMore).toBe(false)
    })
  })

  describe('error toasts (store mutation contract)', () => {
    beforeEach(() => {
      useToastStore.getState().dismissAll()
    })

    it('createDefinition emits an error toast when the API rejects', async () => {
      server.use(
        http.post('/api/v1/workflows', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      await useWorkflowEditorStore
        .getState()
        .createDefinition('broken', 'default')

      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toContain('create')
      // Description is what surfaces the underlying API error to the
      // operator; without this assertion a future change that drops the
      // `description` field would pass the variant/title checks silently.
      expect(toasts[0]!.description).toBeDefined()
    })

    it('saveDefinition emits an error toast on a non-409 failure', async () => {
      // Seed a definition so saveDefinition reaches the network call.
      useWorkflowEditorStore.setState({
        definition: buildWorkflow({ id: 'wf-1', name: 'wf' }),
        nodes: [],
        edges: [],
      })
      server.use(
        http.patch('/api/v1/workflows/:id', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      await useWorkflowEditorStore.getState().saveDefinition()

      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toContain('save')
      expect(toasts[0]!.description).toBeDefined()
    })

    it('saveDefinition emits a warning toast on 409 conflict', async () => {
      useWorkflowEditorStore.setState({
        definition: buildWorkflow({ id: 'wf-1', name: 'wf' }),
        nodes: [],
        edges: [],
      })
      server.use(
        http.patch('/api/v1/workflows/:id', () =>
          HttpResponse.json(apiError('conflict'), { status: 409 }),
        ),
      )

      await useWorkflowEditorStore.getState().saveDefinition()

      const toasts = useToastStore.getState().toasts
      const warning = toasts.find((t) => t.variant === 'warning')
      expect(warning).toBeDefined()
      expect(warning!.title).toBe('Version conflict')
    })

    it('rollback emits an error toast on failure', async () => {
      useWorkflowEditorStore.setState({
        definition: buildWorkflow({ id: 'wf-1', name: 'wf' }),
      })
      server.use(
        http.post('/api/v1/workflows/:id/rollback', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      await useWorkflowEditorStore.getState().rollback(1)

      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title.toLowerCase()).toContain('roll back')
      expect(toasts[0]!.description).toBeDefined()
    })
  })
})
