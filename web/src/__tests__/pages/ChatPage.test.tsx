import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import type { ChiefOfStaffFlags, MetaConfig } from '@/api/endpoints/meta'
import ChatPage from '@/pages/ChatPage'
import { useMetaStore } from '@/stores/meta'

const BASE_FLAGS: ChiefOfStaffFlags = {
  chat_enabled: true,
  propose_enabled: true,
  group_chat_enabled: true,
  direct_mcp_enabled: false,
  chat_model: 'test-provider/example-medium-001',
  propose_model: 'test-provider/example-large-001',
  routing_model: 'test-provider/example-small-001',
  narrative_model: 'test-provider/example-medium-001',
  direct_mcp_ready: false,
}

function configWith(overrides: Partial<ChiefOfStaffFlags>): MetaConfig {
  return {
    enabled: true,
    chief_of_staff_enabled: true,
    chief_of_staff: { ...BASE_FLAGS, ...overrides },
    config_tuning_enabled: false,
    architecture_proposals_enabled: false,
    prompt_tuning_enabled: false,
    code_modification_enabled: false,
  }
}

function renderMode(mode: string, overrides: Partial<ChiefOfStaffFlags>) {
  useMetaStore.setState({ config: configWith(overrides) })
  return render(
    <MemoryRouter initialEntries={[`/?mode=${mode}`]}>
      <ChatPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  useMetaStore.setState({ config: null })
})

describe('ChatPage mode gating', () => {
  it('surfaces the missing-model notice when an enabled mode has no model', () => {
    renderMode('staff', { chat_enabled: true, chat_model: null })
    expect(screen.getByText('No model is configured for this mode')).toBeInTheDocument()
    expect(screen.getByText(/chief_of_staff\.chat_model/)).toBeInTheDocument()
  })

  it('does not gate a mode whose model is configured', () => {
    renderMode('staff', { chat_enabled: true })
    expect(
      screen.queryByText('No model is configured for this mode'),
    ).not.toBeInTheDocument()
  })

  it('warns that direct action is inert without security governance', () => {
    renderMode('action', { direct_mcp_enabled: true, direct_mcp_ready: false })
    expect(screen.getByText('This mode is enabled but not yet live')).toBeInTheDocument()
    expect(screen.getByText(/security\.mcp_self_consumer/)).toBeInTheDocument()
  })

  it('does not warn once direct action is live', () => {
    renderMode('action', { direct_mcp_enabled: true, direct_mcp_ready: true })
    expect(
      screen.queryByText('This mode is enabled but not yet live'),
    ).not.toBeInTheDocument()
  })

  it('shows the switched-off notice when the flag is disabled', () => {
    renderMode('action', { direct_mcp_enabled: false })
    expect(
      screen.getByText('This conversation mode is switched off'),
    ).toBeInTheDocument()
  })
})
