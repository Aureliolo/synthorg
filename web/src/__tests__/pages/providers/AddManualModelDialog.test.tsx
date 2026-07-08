import { describe, expect, it } from "vitest";
import {
  validateManualModel,
  type ManualModelInputs,
} from "@/pages/providers/manual-model";

function inputs(overrides: Partial<ManualModelInputs> = {}): ManualModelInputs {
  return {
    modelId: "example-image-001",
    alias: "",
    costInput: "",
    costOutput: "",
    costImage: "",
    maxContext: "",
    latencyMs: "",
    ...overrides,
  };
}

describe("validateManualModel image capability", () => {
  it("infers supports_image_generation from a configured per-image cost", () => {
    const result = validateManualModel(inputs({ costImage: "0.04" }));
    expect("model" in result).toBe(true);
    if (!("model" in result)) return;
    expect(result.model.cost_per_image).toBe(0.04);
    expect(result.model.metadata.supports_image_generation).toBe(true);
  });

  it("leaves supports_image_generation false when no per-image cost is set", () => {
    const result = validateManualModel(inputs({ costImage: "" }));
    expect("model" in result).toBe(true);
    if (!("model" in result)) return;
    expect(result.model.cost_per_image).toBeNull();
    expect(result.model.metadata.supports_image_generation).toBe(false);
  });
});
