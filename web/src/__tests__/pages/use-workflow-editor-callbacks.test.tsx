import { act, renderHook } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useWorkflowEditorCallbacks } from '@/pages/workflow-editor/useWorkflowEditorCallbacks'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { useToastStore } from '@/stores/toast'

function buildArgs(
  validate: () => Promise<void>,
): Parameters<typeof useWorkflowEditorCallbacks>[0] {
  return {
    selectedNodeId: null,
    addNode: vi.fn(),
    selectNode: vi.fn(),
    updateNodeConfig: vi.fn(),
    exportYaml: vi.fn(() => Promise.resolve('')),
    saveDefinition: vi.fn(() => Promise.resolve(true)),
    validate,
    saveViewport: vi.fn(),
  }
}

describe('useWorkflowEditorCallbacks handleValidate', () => {
  beforeEach(() => {
    useWorkflowEditorStore.getState().reset()
    useToastStore.getState().dismissAll()
  })

  it('emits an error toast when validation fails (previously silent)', async () => {
    // Mirror the store contract: every ``validate`` failure path clears
    // ``validationResult`` and sets ``error``.
    const validate = vi.fn(() => {
      useWorkflowEditorStore.setState({
        error: 'Cannot validate: no workflow loaded',
        validationResult: null,
      })
      return Promise.resolve()
    })
    const { result } = renderHook(
      () => useWorkflowEditorCallbacks(buildArgs(validate)),
      { wrapper: MemoryRouter },
    )

    await act(async () => {
      await result.current.handleValidate()
    })

    const toasts = useToastStore.getState().toasts
    const failure = toasts.find((t) => t.variant === 'error')
    expect(failure).toBeDefined()
    expect(failure?.title).toContain('Validation failed')
    expect(failure?.description).toBe('Cannot validate: no workflow loaded')
  })

  it('emits a success toast when the workflow is valid', async () => {
    const validate = vi.fn(() => {
      useWorkflowEditorStore.setState({
        validationResult: { valid: true, errors: [] },
        error: null,
      })
      return Promise.resolve()
    })
    const { result } = renderHook(
      () => useWorkflowEditorCallbacks(buildArgs(validate)),
      { wrapper: MemoryRouter },
    )

    await act(async () => {
      await result.current.handleValidate()
    })

    const toasts = useToastStore.getState().toasts
    const success = toasts.find((t) => t.variant === 'success')
    expect(success).toBeDefined()
    expect(success?.title).toContain('valid')
  })
})
