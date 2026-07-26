import { render, screen } from '@testing-library/react'
import { AgentConfigCard } from '@/components/agents/AgentConfigCard'
import { makeAgent } from '../../helpers/factories'

/**
 * The card is a pure mapping from a domain agent onto the shared
 * ``AgentCard``, so what it must be tested for is the mapping itself: each
 * capability status the backend can report has to reach a distinct piece of
 * card wording. An accessor that exists but is never passed through leaves the
 * dashboard silently rendering an outage as a healthy agent.
 */
describe('AgentConfigCard', () => {
  it('reports a provider-config outage rather than a broken binding', () => {
    render(
      <AgentConfigCard
        agent={makeAgent('Ada', {
          model_capabilities: null,
          model_capability_status: 'provider_config_unavailable',
        })}
      />,
    )

    expect(screen.getByText('provider config unavailable')).toBeInTheDocument()
    expect(screen.queryByText('model not found')).not.toBeInTheDocument()
  })

  it('reports an unresolved binding as a missing model', () => {
    render(
      <AgentConfigCard
        agent={makeAgent('Ada', {
          model_capabilities: null,
          model_capability_status: 'unresolved',
        })}
      />,
    )

    expect(screen.getByText('model not found')).toBeInTheDocument()
  })

  it('shows the resolved capabilities when the provider config is readable', () => {
    render(
      <AgentConfigCard
        agent={makeAgent('Ada', {
          model_capabilities: {
            supports_reasoning: true,
            supports_vision: true,
            tool_calling: 'verified',
            metadata_source: 'probe',
          },
          model_capability_status: 'resolved',
        })}
      />,
    )

    expect(screen.getByText('reasoning, vision')).toBeInTheDocument()
    expect(screen.queryByText('provider config unavailable')).not.toBeInTheDocument()
  })
})
