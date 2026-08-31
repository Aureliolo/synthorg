import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ProviderModelList } from '@/pages/providers/ProviderModelList'
import type { ProviderModelResponse } from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

function buildModel(
  id: string,
  toolCallsVerified: boolean | null,
): ProviderModelResponse {
  return {
    id,
    alias: null,
    capability_overrides: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: null,
    local_params: null,
    supports_tools: true,
    tool_calls_verified: toolCallsVerified,
    supports_vision: false,
    supports_streaming: true,
    supports_embeddings: false,
    supports_reasoning: false,
    supports_image_generation: false,
    supports_prompt_caching: false,
    family: null,
    metadata_source: 'unknown',
    stale: null,
  }
}

describe('ProviderModelList tool-calling unavailable', () => {
  it('shows the badge for a runtime-downgraded model', () => {
    render(<ProviderModelList models={[buildModel('downgraded', false)]} />)
    expect(screen.getByText('No tool calling')).toBeInTheDocument()
  })

  it('omits the badge for a healthy model', () => {
    render(<ProviderModelList models={[buildModel('healthy', null)]} />)
    expect(screen.queryByText('No tool calling')).not.toBeInTheDocument()
  })

  it('invokes onReenableToolCalling when the re-enable action is clicked', async () => {
    const onReenable = vi.fn()
    render(
      <ProviderModelList
        models={[buildModel('downgraded', false)]}
        onReenableToolCalling={onReenable}
      />,
    )
    await userEvent.click(
      screen.getByRole('button', { name: /Re-enable tool calling for downgraded/ }),
    )
    expect(onReenable).toHaveBeenCalledWith('downgraded')
  })

  it('shows no re-enable action for a healthy model', () => {
    render(
      <ProviderModelList
        models={[buildModel('healthy', null)]}
        onReenableToolCalling={vi.fn()}
      />,
    )
    expect(
      screen.queryByRole('button', { name: /Re-enable tool calling/ }),
    ).not.toBeInTheDocument()
  })

  it('keeps every row at the header column count when only some rows have actions', () => {
    // The Actions column appears because one model is downgraded; a healthy
    // row in the same (no delete / no config) table must still render the
    // Actions cell (empty) so its column count matches the header.
    render(
      <ProviderModelList
        models={[buildModel('downgraded', false), buildModel('healthy', null)]}
        onReenableToolCalling={vi.fn()}
      />,
    )
    const headerCols = screen.getAllByRole('columnheader').length
    for (const row of screen.getAllByRole('row').slice(1)) {
      expect(row.querySelectorAll('td')).toHaveLength(headerCols)
    }
  })
})
