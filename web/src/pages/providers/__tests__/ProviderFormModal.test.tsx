import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type {
  CloudPreset,
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProviderConfig,
} from '@/api/types/providers'
import { ProviderFormModal } from '../ProviderFormModal'
import type { ProviderFormOverrides } from '../provider-form-helpers'

const exampleProvider: CloudPreset = {
  kind: 'cloud',
  name: 'example-provider',
  display_name: 'Example Provider',
  description: 'Example cloud models',
  driver: 'litellm',
  litellm_provider: 'example-provider',
  auth_type: 'api_key',
  supported_auth_types: ['api_key', 'subscription'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  default_models: [],
}

const cloudApiKeyOnly: CloudPreset = {
  kind: 'cloud',
  name: 'cloud-apikey-only',
  display_name: 'Cloud API-Key Provider',
  description: 'API-key-only cloud provider',
  driver: 'litellm',
  litellm_provider: 'cloud-apikey-only',
  auth_type: 'api_key',
  supported_auth_types: ['api_key'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  default_models: [],
}

function makeOverrides(extra: Partial<ProviderFormOverrides> = {}): ProviderFormOverrides {
  return {
    presets: [exampleProvider, cloudApiKeyOnly],
    presetsLoading: false,
    presetsError: null,
    onFetchPresets: vi.fn(),
    onCreateFromPreset: vi.fn<
      (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
    >(() => Promise.resolve(null)),
    onCreateProvider: vi.fn<
      (data: CreateProviderRequest) => Promise<ProviderConfig | null>
    >(() => Promise.resolve(null)),
    ...extra,
  }
}

describe('ProviderFormModal: initialPreset prop', () => {
  it('pre-fills the form when opened with a preset', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="example-provider"
        overrides={makeOverrides()}
      />,
    )

    const nameInput = await screen.findByLabelText<HTMLInputElement>(/Provider Name/i)
    expect(nameInput.value).toBe('example-provider')
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

    expect(await screen.findByLabelText(/Or pick a preset/i)).toBeInTheDocument()
  })
})

describe('ProviderFormModal: subscription billing banner', () => {
  it('renders the credits banner for any subscription-supporting preset, named by display_name', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="example-provider"
        overrides={makeOverrides()}
      />,
    )

    const select = await screen.findByLabelText<HTMLSelectElement>(/Authentication/i)
    fireEvent.change(select, { target: { value: 'subscription' } })

    expect(
      await screen.findByText(/Counts against your subscription credits/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/Example Provider/i)).toBeInTheDocument()
    // The vendor pricing anchor was removed; no external pricing link.
    expect(screen.queryByRole('link', { name: /pricing/i })).not.toBeInTheDocument()
  })

  it('does NOT render the billing banner when the preset does not support subscription auth', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="cloud-apikey-only"
        overrides={makeOverrides()}
      />,
    )

    await screen.findByLabelText(/Authentication/i)
    expect(
      screen.queryByText(/Counts against your subscription credits/i),
    ).not.toBeInTheDocument()
  })
})

describe('ProviderFormModal: presets fetch storm guard (P0)', () => {
  it('fetches presets at most once while open, even across re-renders', async () => {
    const onFetchPresets = vi.fn()
    const overrides = makeOverrides({ onFetchPresets, presets: [] })

    const { rerender } = render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset={null}
        overrides={overrides}
      />,
    )

    await waitFor(() => expect(onFetchPresets).toHaveBeenCalledTimes(1))

    // A parent re-render mints a fresh overrides object (new callback
    // identities) but must NOT re-fire the fetch effect.
    rerender(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset={null}
        overrides={makeOverrides({ onFetchPresets, presets: [] })}
      />,
    )

    expect(onFetchPresets).toHaveBeenCalledTimes(1)
  })
})

describe('ProviderFormModal: correctness', () => {
  it('disables Create until an API key is supplied', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset={null}
        overrides={makeOverrides()}
      />,
    )

    // Custom mode defaults to api_key auth with a blank key -> blocked.
    const nameInput = await screen.findByLabelText<HTMLInputElement>(/Provider Name/i)
    fireEvent.change(nameInput, { target: { value: 'my-provider' } })
    const createBtn = screen.getByRole('button', { name: /Create Provider/i })
    expect(createBtn).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/API Key/i), { target: { value: 'sk-test' } })
    expect(createBtn).toBeEnabled()
  })

  it('renders custom-header credential fields when that auth type is selected', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset={null}
        overrides={makeOverrides()}
      />,
    )

    const select = await screen.findByLabelText<HTMLSelectElement>(/Authentication/i)
    fireEvent.change(select, { target: { value: 'custom_header' } })

    expect(await screen.findByLabelText(/Header Name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Header Value/i)).toBeInTheDocument()
  })

  it('shows an inline error when the wizard reports a submit failure', async () => {
    render(
      <ProviderFormModal
        open
        onClose={() => undefined}
        mode="create"
        initialPreset="example-provider"
        overrides={makeOverrides({ submitError: 'Provider already exists' })}
      />,
    )

    expect(await screen.findByText(/Could not save provider/i)).toBeInTheDocument()
    expect(screen.getByText(/Provider already exists/i)).toBeInTheDocument()
  })
})
