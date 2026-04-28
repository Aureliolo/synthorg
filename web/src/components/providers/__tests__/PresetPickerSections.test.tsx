import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type {
  CloudPreset,
  LocalPreset,
  ProviderConfig,
} from '@/api/types/providers'
import { PresetPickerSections } from '../PresetPickerSections'

const cloud: CloudPreset = {
  kind: 'cloud',
  name: 'anthropic',
  display_name: 'Anthropic',
  description: 'Claude models',
  driver: 'litellm',
  litellm_provider: 'anthropic',
  auth_type: 'api_key',
  supported_auth_types: ['api_key'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  default_models: [],
}

const ollama: LocalPreset = {
  kind: 'local',
  name: 'ollama',
  display_name: 'Ollama',
  description: 'Local Ollama',
  driver: 'litellm',
  litellm_provider: 'ollama',
  auth_type: 'none',
  default_base_url: 'http://localhost:11434',
  requires_base_url: true,
  is_featured: true,
  candidate_urls: ['http://localhost:11434'],
  supports_model_pull: true,
  supports_model_delete: true,
  supports_model_config: true,
}

const vllm: LocalPreset = {
  kind: 'local',
  name: 'vllm',
  display_name: 'vLLM',
  description: 'vLLM',
  driver: 'litellm',
  litellm_provider: 'openai',
  auth_type: 'none',
  default_base_url: 'http://localhost:8000/v1',
  requires_base_url: true,
  is_featured: true,
  candidate_urls: [],
  supports_model_pull: false,
  supports_model_delete: false,
  supports_model_config: false,
}

const noProviders: Record<string, ProviderConfig> = {}

function makeProps(overrides: Partial<React.ComponentProps<typeof PresetPickerSections>> = {}) {
  return {
    presets: [cloud, ollama, vllm],
    probeResults: {} as Readonly<Record<string, never>>,
    probing: false,
    providers: noProviders,
    onSelectCloud: vi.fn(),
    onAddLocal: vi.fn(),
    onAddCloudCounterpart: vi.fn(),
    onReprobe: vi.fn(),
    onConfigureManually: vi.fn(),
    ...overrides,
  }
}

describe('PresetPickerSections', () => {
  it('renders the cloud grid with each cloud preset card', () => {
    render(<PresetPickerSections {...makeProps()} />)
    expect(screen.getByText('Anthropic')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Anthropic/ })).toBeInTheDocument()
  })

  it('hides the detected list entirely when probing is idle and nothing was found', () => {
    render(<PresetPickerSections {...makeProps()} />)
    expect(screen.queryByText(/Detected on this machine/)).not.toBeInTheDocument()
  })

  it('renders the detected list when a local preset returns a hit', () => {
    render(
      <PresetPickerSections
        {...makeProps({
          probeResults: {
            ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
          },
        })}
      />,
    )
    expect(screen.getByText(/Detected on this machine/)).toBeInTheDocument()
    expect(screen.getByText(/at http:\/\/localhost:11434/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add local' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add cloud' })).toBeInTheDocument()
  })

  it('does not render vLLM in the detected list (no candidate URLs)', () => {
    render(
      <PresetPickerSections
        {...makeProps({
          probeResults: {
            vllm: { url: 'http://localhost:8000/v1', model_count: 0, candidates_tried: 0 },
          },
        })}
      />,
    )
    // Even when probeResults references vllm, the section filters to
    // local presets that have candidate URLs -- vLLM is omitted by
    // construction.
    expect(screen.queryByText('vLLM')).not.toBeInTheDocument()
  })

  it('invokes onSelectCloud when a cloud card is clicked', () => {
    const onSelectCloud = vi.fn()
    render(<PresetPickerSections {...makeProps({ onSelectCloud })} />)
    fireEvent.click(screen.getByRole('button', { name: /Add Anthropic/ }))
    expect(onSelectCloud).toHaveBeenCalledWith('anthropic')
  })

  it('invokes onAddLocal with the detected URL when [Add local] is clicked', () => {
    const onAddLocal = vi.fn()
    render(
      <PresetPickerSections
        {...makeProps({
          onAddLocal,
          probeResults: {
            ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
          },
        })}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add local' }))
    expect(onAddLocal).toHaveBeenCalledWith('ollama', 'http://localhost:11434')
  })

  it('invokes onAddCloudCounterpart with the cloud preset name when [Add cloud] is clicked', () => {
    const onAddCloudCounterpart = vi.fn()
    render(
      <PresetPickerSections
        {...makeProps({
          onAddCloudCounterpart,
          probeResults: {
            ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
          },
        })}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add cloud' }))
    expect(onAddCloudCounterpart).toHaveBeenCalledWith('ollama-cloud')
  })

  it('invokes onConfigureManually when the manual button is clicked', () => {
    const onConfigureManually = vi.fn()
    render(<PresetPickerSections {...makeProps({ onConfigureManually })} />)
    fireEvent.click(screen.getByRole('button', { name: /Configure manually/ }))
    expect(onConfigureManually).toHaveBeenCalledTimes(1)
  })

  it('hides the More providers section when no soft (non-featured) cloud presets are present', () => {
    // All fixtures here are featured (is_featured=true), so the
    // collapsible "More providers via LiteLLM" surface should not
    // render.
    render(<PresetPickerSections {...makeProps()} />)
    expect(
      screen.queryByText(/More providers via LiteLLM/),
    ).not.toBeInTheDocument()
  })

  it('renders the More providers section when soft cloud presets exist', () => {
    const softCloud: CloudPreset = {
      kind: 'cloud',
      name: 'softprovider',
      display_name: 'Softprovider',
      description: "Models served via LiteLLM provider 'softprovider'",
      driver: 'litellm',
      litellm_provider: 'softprovider',
      auth_type: 'api_key',
      supported_auth_types: ['api_key'],
      default_base_url: null,
      requires_base_url: false,
      is_featured: false,
      default_models: [],
    }
    render(
      <PresetPickerSections
        {...makeProps({ presets: [cloud, softCloud, ollama, vllm] })}
      />,
    )
    // Summary text is visible immediately (count in label).
    const summary = screen.getByText(/More providers via LiteLLM \(1\)/)
    // Click to expand the <details>; jsdom keeps closed descendants
    // in the DOM, so without this click the assertion below would
    // pass even if the toggle stopped revealing its content.
    fireEvent.click(summary)
    expect(
      screen.getByRole('button', { name: /Add Softprovider/ }),
    ).toBeInTheDocument()
  })

  it('shows only successful detected rows when probe returned mixed results', () => {
    // ollama probe succeeded; lm-studio probe failed.  The component
    // should render Ollama in the detected list and silently omit
    // LM Studio (it does not appear as an X mark).  The section
    // header still renders because at least one preset succeeded.
    render(
      <PresetPickerSections
        {...makeProps({
          probeResults: {
            ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
          },
        })}
      />,
    )
    expect(screen.getByText(/Detected on this machine/)).toBeInTheDocument()
    expect(screen.getByText(/at http:\/\/localhost:11434/)).toBeInTheDocument()
    // LM Studio is filtered out: it returned no URL, so no row is rendered.
    expect(screen.queryByText('LM Studio')).not.toBeInTheDocument()
  })
})
