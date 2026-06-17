import type { Meta, StoryObj } from "@storybook/react-vite";
import { ProviderCard } from "./ProviderCard";
import type { ProviderWithName } from "@/utils/providers";
import type { ProviderHealthSummary } from "@/api/types/providers";

const baseProvider: ProviderWithName = {
  name: "anthropic",
  driver: "litellm",
  litellm_provider: "anthropic",
  auth_type: "api_key",
  base_url: null,
  models: [
    {
      id: "claude-sonnet-4-20250514",
      alias: "sonnet",
      cost_per_1k_input: 0.003,
      cost_per_1k_output: 0.015,
      max_context: 200000,
      estimated_latency_ms: null,
      local_params: null,
      metadata: {
        supports_tools: false,
        supports_vision: false,
        supports_reasoning: false,
        max_output_tokens: null,
        family: null,
        generation: null,
        release_date: null,
        metadata_source: "unknown",
      },
    },
    {
      id: "claude-haiku-4-5-20251001",
      alias: "haiku",
      cost_per_1k_input: 0.0008,
      cost_per_1k_output: 0.004,
      max_context: 200000,
      estimated_latency_ms: null,
      local_params: null,
      metadata: {
        supports_tools: false,
        supports_vision: false,
        supports_reasoning: false,
        max_output_tokens: null,
        family: null,
        generation: null,
        release_date: null,
        metadata_source: "unknown",
      },
    },
  ],
  has_api_key: true,
  has_oauth_credentials: false,
  has_custom_header: false,
  has_subscription_token: false,
  tos_accepted_at: null,
  oauth_token_url: null,
  oauth_client_id: null,
  oauth_scope: null,
  custom_header_name: null,
  preset_name: null,
  supports_model_pull: false,
  supports_model_delete: false,
  supports_model_config: false,
};

const healthUp: ProviderHealthSummary = {
  last_check_timestamp: "2026-03-27T12:00:00Z",
  avg_response_time_ms: 250,
  error_rate_percent_24h: 0.5,
  calls_last_24h: 1234,
  health_status: "up",
  total_tokens_24h: 245000,
  total_cost_24h: 3.72,
};

const meta = {
  title: "Providers/ProviderCard",
  component: ProviderCard,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ProviderCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Healthy: Story = {
  args: { provider: baseProvider, health: healthUp },
};
export const Degraded: Story = {
  args: {
    provider: baseProvider,
    health: {
      ...healthUp,
      health_status: "degraded",
      error_rate_percent_24h: 15.2,
    },
  },
};
export const Down: Story = {
  args: {
    provider: baseProvider,
    health: {
      ...healthUp,
      health_status: "down",
      error_rate_percent_24h: 75.0,
    },
  },
};
export const NoHealth: Story = {
  args: { provider: baseProvider, health: null },
};
export const LocalProvider: Story = {
  args: {
    provider: {
      ...baseProvider,
      name: "ollama-local",
      litellm_provider: "ollama",
      auth_type: "none",
      base_url: "http://localhost:11434",
      models: [],
      has_api_key: false,
      supports_model_pull: true,
      supports_model_delete: true,
      supports_model_config: true,
    },
    health: null,
  },
};
