import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { UseProviderDetailDataReturn } from "@/hooks/useProviderDetailData";
import type { ProviderModelResponse } from "@/api/types/providers";
import type { ProviderWithName } from "@/utils/providers";
import { DEFAULT_CURRENCY } from "@/utils/currencies";

let hookReturn: UseProviderDetailDataReturn;

const getDetailData = vi.fn(() => hookReturn);
vi.mock("@/hooks/useProviderDetailData", () => {
  const hookName = "useProviderDetailData";
  return { [hookName]: () => getDetailData() };
});

const { default: ProviderDetailPage } =
  await import("@/pages/ProviderDetailPage");

function makeProvider(name: string): ProviderWithName {
  return {
    name,
    driver: "litellm",
    litellm_provider: "test-provider",
    auth_type: "api_key",
    base_url: null,
    models: [
      {
        id: "test-model",
        alias: "test",
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
}

const testModels: ProviderModelResponse[] = [
  {
    id: "test-model",
    alias: "test",
    cost_per_1k_input: 0.003,
    cost_per_1k_output: 0.015,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: null,
    local_params: null,
    supports_tools: true,
    supports_vision: false,
    supports_streaming: true,
  },
];

const defaultReturn: UseProviderDetailDataReturn = {
  provider: null,
  models: [],
  health: null,
  loading: false,
  error: null,
  testConnectionResult: null,
  testingConnection: false,
};

function renderDetail(name = "test-provider") {
  return render(
    <MemoryRouter initialEntries={[`/providers/${name}`]}>
      <Routes>
        <Route
          path="/providers/:providerName"
          element={<ProviderDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProviderDetailPage", () => {
  beforeEach(() => {
    hookReturn = { ...defaultReturn };
    vi.clearAllMocks();
  });

  it("renders loading skeleton when loading", () => {
    hookReturn = { ...defaultReturn, loading: true };
    renderDetail();
    expect(
      screen.getByLabelText("Loading provider details"),
    ).toBeInTheDocument();
  });

  it("renders error message when error without provider", () => {
    hookReturn = { ...defaultReturn, error: "Provider not found" };
    renderDetail();
    expect(screen.getByText("Provider not found")).toBeInTheDocument();
  });

  it("renders provider name when data loaded", () => {
    const provider = makeProvider("test-provider");
    hookReturn = {
      ...defaultReturn,
      provider,
      models: testModels,
    };
    renderDetail();
    expect(
      screen.getByRole("heading", { name: "test-provider" }),
    ).toBeInTheDocument();
  });

  it("renders model list when models present", () => {
    const provider = makeProvider("test-provider");
    hookReturn = {
      ...defaultReturn,
      provider,
      models: testModels,
    };
    renderDetail();
    expect(screen.getByText("test-model")).toBeInTheDocument();
    expect(screen.getByText("tools")).toBeInTheDocument();
    expect(screen.getByText("stream")).toBeInTheDocument();
  });

  it("renders health metrics when health available", () => {
    const provider = makeProvider("test-provider");
    hookReturn = {
      ...defaultReturn,
      provider,
      models: [],
      health: {
        last_check_timestamp: "2026-03-27T12:00:00Z",
        avg_response_time_ms: 250,
        error_rate_percent_24h: 1.5,
        calls_last_24h: 500,
        health_status: "up",
        total_tokens_24h: 50000,
        total_cost_24h: 1.25,
      },
    };
    renderDetail();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("250ms")).toBeInTheDocument();
    expect(screen.getByText(/^50K$/i)).toBeInTheDocument();
    expect(screen.getByText(/1\.25/)).toBeInTheDocument();
  });

  it("renders unknown health status indicator", () => {
    const provider = makeProvider("test-provider");
    hookReturn = {
      ...defaultReturn,
      provider,
      models: [],
      health: {
        last_check_timestamp: null,
        avg_response_time_ms: null,
        error_rate_percent_24h: 0,
        calls_last_24h: 0,
        health_status: "unknown",
        total_tokens_24h: 0,
        total_cost_24h: 0,
      },
    };
    renderDetail();
    expect(screen.getByText(/unknown/i)).toBeInTheDocument();
  });

  it("renders test connection result when present", () => {
    const provider = makeProvider("test-provider");
    hookReturn = {
      ...defaultReturn,
      provider,
      models: [],
      testConnectionResult: {
        success: true,
        latency_ms: 123,
        error: null,
        model_tested: "test-model",
      },
    };
    renderDetail();
    expect(screen.getByText(/Connected/)).toBeInTheDocument();
  });
});
