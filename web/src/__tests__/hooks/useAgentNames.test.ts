import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useAgentNames } from '@/hooks/useAgentNames'
import { useAgentsStore } from '@/stores/agents'

import { makeAgent } from '../helpers/factories'

describe('useAgentNames', () => {
  beforeEach(() => {
    useAgentsStore.setState({ agents: [] })
  })

  it('resolves an agent id to the name the operator knows', async () => {
    // A project card, a task-board filter and an approvals row all carry the
    // id, and an operator cannot identify an agent from one.
    useAgentsStore.setState({ agents: [makeAgent('Feline Rek')] })

    const { result } = renderHook(() => useAgentNames())

    await waitFor(() => {
      expect(result.current.ready).toBe(true)
    })
    expect(result.current.nameOf('agent-Feline Rek')).toBe('Feline Rek')
  })

  it('leaves a value the roster does not know alone', async () => {
    // Two cases, and both want the value shown: a system actor is already a
    // readable word, and an unloaded id is better shown than replaced by a
    // placeholder that hides which row is which.
    useAgentsStore.setState({ agents: [makeAgent('Feline Rek')] })

    const { result } = renderHook(() => useAgentNames())

    await waitFor(() => {
      expect(result.current.ready).toBe(true)
    })
    expect(result.current.nameOf('coordinator')).toBe('coordinator')
    expect(result.current.nameOf('agent-unloaded')).toBe('agent-unloaded')
  })

  it('resolves an absent id to nothing rather than the string "null"', async () => {
    useAgentsStore.setState({ agents: [makeAgent('Feline Rek')] })

    const { result } = renderHook(() => useAgentNames())

    await waitFor(() => {
      expect(result.current.ready).toBe(true)
    })
    expect(result.current.nameOf(null)).toBe('')
    expect(result.current.nameOf(undefined)).toBe('')
  })
})
