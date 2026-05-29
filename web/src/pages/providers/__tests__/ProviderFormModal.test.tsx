import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type {
  CloudPreset,
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProviderConfig,
} from '@/api/types/providers'
import { ProviderFormModal } from '../ProviderFormModal'
import type { ProviderFormOverrides } from '../provider-form-helpers'

const anthropic: CloudPreset = {
  kind: 'cloud',
  name: 'anthropic',
  display_name: 'Anthropic',
  description: 'Claude models',
  driver: 'litellm',
  litellm_provider: 'anthropic',
  auth_type: 'api_key',
  supported_auth_types: ['api_key', 'subscription'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  default_models: [],
}

const openai: CloudPreset = {
  kind: 'cloud',
  name: 'openai',
  display_name: 'OpenAI',
  description: 'GPT models',
  driver: 'litellm',
  litellm_provider: 'openai',
  auth_type: 'api_key',
  supported_auth_types: ['api_key'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  default_models: [],
}

function makeOverrides(): ProviderFormOverrides {
  return {
    presets: [anthropic, openai],
    presetsLoading: false,
    presetsError: null,
    onFetchPresets: vi.fn(),
    onCreateFromPreset: vi.fn<
      (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
    >(async () => null),
    onCreateProvider: vi.fn<
      (data: CreateProviderRequest) => Promise<ProviderConfig | null>
    >(async () => null),
  }
}

describe('ProviderFormModal: initialPreset prop', () => {
  it('pre-fills the form when opened with initialPreset="anthropic"', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="anthropic"
        overrides={makeOverrides()}
      />,
    )

    // Provider Name field is pre-filled with the preset name after the
    // render-phase state sync commits.
    const nameInput = (await screen.findByLabelText(
      /Provider Name/i,
    )) as HTMLInputElement
    expect(nameInput.value).toBe('anthropic')

    // The "Or pick a preset" custom-mode dropdown is NOT shown (a
    // preset was already selected).
    expect(screen.queryByLabelText(/Or pick a preset/i)).not.toBeInTheDocument()
  })

  it('opens in custom-endpoint mode when initialPreset is null', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset={null}
        overrides={makeOverrides()}
      />,
    )

    // Custom mode shows the "Or pick a preset" dropdown so the user
    // can opt into a preset without going back to the picker.
    expect(
      await screen.findByLabelText(/Or pick a preset/i),
    ).toBeInTheDocument()
  })
})

describe('ProviderFormModal: Anthropic subscription billing banner', () => {
  it('renders the billing-context info banner for Anthropic when subscription auth is selected', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="anthropic"
        overrides={makeOverrides()}
      />,
    )

    const select = (await screen.findByLabelText(
      /Authentication/i,
    )) as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'subscription' } })

    expect(
      await screen.findByText(/Counts against your subscription credits/i),
    ).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /View pricing/i }) as HTMLAnchorElement
    expect(link.href).toContain('anthropic.com/pricing')
  })

  it('does NOT render the billing banner when the preset does not support subscription auth', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="openai"
        overrides={makeOverrides()}
      />,
    )

    // Wait for the form to commit, then assert the banner is absent.
    await screen.findByLabelText(/Authentication/i)
    expect(
      screen.queryByText(/Counts against your subscription credits/i),
    ).not.toBeInTheDocument()
  })

  it('does NOT render the billing banner with API-key auth (default)', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="anthropic"
        overrides={makeOverrides()}
      />,
    )

    // Default auth type is 'api_key' on the Anthropic preset; the
    // banner is gated on subscription auth, so it should not show.
    await screen.findByLabelText(/Authentication/i)
    expect(
      screen.queryByText(/Counts against your subscription credits/i),
    ).not.toBeInTheDocument()
  })
})
