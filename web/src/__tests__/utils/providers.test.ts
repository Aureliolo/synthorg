import {
  normalizeProviders,
  filterProviders,
  sortProviders,
  getProviderHealthColor,
  formatLatency,
  formatErrorRate,
  formatTokenCount,
  formatCost,
} from "@/utils/providers";
import type {
  ProviderConfig,
  ProviderHealthSummary,
} from "@/api/types/providers";
import type { ProviderWithName } from "@/utils/providers";

// ── Factories ──────────────────────────────────────────────

function makeConfig(overrides: Partial<ProviderConfig> = {}): ProviderConfig {
  return {
    name: null,
    driver: "litellm",
    litellm_provider: null,
    auth_type: "api_key",
    base_url: null,
    models: [],
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
    ...overrides,
  };
}

function makeProvider(
  name: string,
  overrides: Partial<ProviderConfig> = {},
): ProviderWithName {
  return { ...makeConfig(overrides), name };
}

function makeHealth(
  overrides: Partial<ProviderHealthSummary> = {},
): ProviderHealthSummary {
  return {
    last_check_timestamp: "2026-03-27T12:00:00Z",
    avg_response_time_ms: 250,
    error_rate_percent_24h: 0,
    calls_last_24h: 100,
    health_status: "up",
    total_tokens_24h: 0,
    total_cost_24h: 0,
    ...overrides,
  };
}

// ── normalizeProviders ────────────────────────────────────

describe("normalizeProviders", () => {
  it("converts record to array with name field", () => {
    const record = {
      "test-provider": makeConfig(),
      "other-provider": makeConfig({ driver: "litellm" }),
    };
    const result = normalizeProviders(record);
    expect(result).toHaveLength(2);
    expect(result[0]!.name).toBe("test-provider");
    expect(result[1]!.name).toBe("other-provider");
  });

  it("returns empty array for empty record", () => {
    expect(normalizeProviders({})).toEqual([]);
  });
});

// ── filterProviders ───────────────────────────────────────

describe("filterProviders", () => {
  const providers: ProviderWithName[] = [
    makeProvider("anthropic", {
      litellm_provider: "anthropic",
      base_url: "https://api.anthropic.com",
    }),
    makeProvider("ollama-local", {
      litellm_provider: "ollama",
      base_url: "http://localhost:11434",
    }),
    makeProvider("openai", { litellm_provider: "openai" }),
  ];
  const healthMap: Record<string, ProviderHealthSummary> = {
    anthropic: makeHealth({ health_status: "up" }),
    "ollama-local": makeHealth({ health_status: "degraded" }),
    openai: makeHealth({ health_status: "down" }),
  };

  it("returns all providers when no filters", () => {
    expect(filterProviders(providers, healthMap, {})).toHaveLength(3);
  });

  it("filters by name search", () => {
    const result = filterProviders(providers, healthMap, { search: "ollama" });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("ollama-local");
  });

  it("filters by litellm_provider search", () => {
    const result = filterProviders(providers, healthMap, {
      search: "anthropic",
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("anthropic");
  });

  it("filters by base_url search", () => {
    const result = filterProviders(providers, healthMap, {
      search: "localhost",
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("ollama-local");
  });

  it("filters by health status", () => {
    const result = filterProviders(providers, healthMap, { health: "up" });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("anthropic");
  });

  it("combines search and health filters", () => {
    const result = filterProviders(providers, healthMap, {
      search: "o",
      health: "down",
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("openai");
  });

  it("is case-insensitive for search", () => {
    const result = filterProviders(providers, healthMap, { search: "OLLAMA" });
    expect(result).toHaveLength(1);
  });
});

// ── sortProviders ─────────────────────────────────────────

describe("sortProviders", () => {
  const providers: ProviderWithName[] = [
    makeProvider("beta", {
      models: [
        {
          id: "m1",
          alias: null,
          cost_per_1k_input: 0,
          cost_per_1k_output: 0,
          max_context: 100000,
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
          stale: null,
        },
      ],
    }),
    makeProvider("alpha"),
    makeProvider("gamma", {
      models: [
        {
          id: "m1",
          alias: null,
          cost_per_1k_input: 0,
          cost_per_1k_output: 0,
          max_context: 100000,
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
          stale: null,
        },
        {
          id: "m2",
          alias: null,
          cost_per_1k_input: 0,
          cost_per_1k_output: 0,
          max_context: 100000,
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
          stale: null,
        },
      ],
    }),
  ];
  const healthMap: Record<string, ProviderHealthSummary> = {
    beta: makeHealth({ health_status: "down" }),
    alpha: makeHealth({ health_status: "up" }),
    gamma: makeHealth({ health_status: "degraded" }),
  };

  it("sorts by name ascending", () => {
    const result = sortProviders(providers, healthMap, "name", "asc");
    expect(result.map((p) => p.name)).toEqual(["alpha", "beta", "gamma"]);
  });

  it("sorts by name descending", () => {
    const result = sortProviders(providers, healthMap, "name", "desc");
    expect(result.map((p) => p.name)).toEqual(["gamma", "beta", "alpha"]);
  });

  it("sorts by model_count ascending", () => {
    const result = sortProviders(providers, healthMap, "model_count", "asc");
    expect(result.map((p) => p.name)).toEqual(["alpha", "beta", "gamma"]);
  });

  it("sorts by health ascending (down first)", () => {
    const result = sortProviders(providers, healthMap, "health", "asc");
    expect(result.map((p) => p.name)).toEqual(["beta", "gamma", "alpha"]);
  });
});

// ── getProviderHealthColor ────────────────────────────────

describe("getProviderHealthColor", () => {
  it("maps up to success", () => {
    expect(getProviderHealthColor("up")).toBe("success");
  });

  it("maps degraded to warning", () => {
    expect(getProviderHealthColor("degraded")).toBe("warning");
  });

  it("maps down to danger", () => {
    expect(getProviderHealthColor("down")).toBe("danger");
  });

  it("maps unknown to muted", () => {
    expect(getProviderHealthColor("unknown")).toBe("muted");
  });
});

// ── formatLatency ─────────────────────────────────────────

describe("formatLatency", () => {
  it("returns -- for null", () => {
    expect(formatLatency(null)).toBe("--");
  });

  it("formats sub-second as ms", () => {
    expect(formatLatency(250)).toBe("250ms");
  });

  it("formats seconds with one decimal", () => {
    expect(formatLatency(1500)).toBe("1.5s");
  });

  it("rounds sub-second values", () => {
    expect(formatLatency(123.7)).toBe("124ms");
  });
});

// ── formatErrorRate ───────────────────────────────────────

describe("formatErrorRate", () => {
  it("shows 0% for zero", () => {
    expect(formatErrorRate(0)).toBe("0%");
  });

  it("shows <0.1% for tiny rates", () => {
    expect(formatErrorRate(0.05)).toBe("<0.1%");
  });

  it("formats with one decimal", () => {
    expect(formatErrorRate(12.34)).toBe("12.3%");
  });
});

// ── formatTokenCount ─────────────────────────────────────

describe("formatTokenCount", () => {
  it("returns 0 for zero", () => {
    expect(formatTokenCount(0)).toBe("0");
  });

  it("formats thousands with K suffix", () => {
    expect(formatTokenCount(50_000)).toMatch(/^50K$/i);
  });

  it("formats millions with M suffix", () => {
    expect(formatTokenCount(1_234_567)).toMatch(/^1\.2M$/i);
  });

  it("formats small numbers with locale string", () => {
    expect(formatTokenCount(500)).toBe("500");
  });
});

// ── formatCost ───────────────────────────────────────────

describe("formatCost", () => {
  it("returns EUR 0.00 for zero", () => {
    expect(formatCost(0)).toContain("0.00");
  });

  it("returns sub-cent indicator for tiny costs", () => {
    expect(formatCost(0.005)).toMatch(/^<.*0\.01$/);
  });

  it("formats with currency symbol", () => {
    const result = formatCost(1.25);
    expect(result).toContain("1.25");
  });

  it("formats larger amounts", () => {
    const result = formatCost(99.99);
    expect(result).toContain("99.99");
  });
});
