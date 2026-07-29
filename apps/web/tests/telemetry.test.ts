import { describe, expect, it } from "vitest";

import { shouldShowFallbackChip } from "@/lib/telemetry";
import type { Interpretation } from "@/types/api";

/**
 * Correction 5.
 *
 * "Handled with safe fallback" is a claim that something failed. Keying it off
 * `effectiveProvider !== configuredProvider` would put it on almost every
 * message, because the deterministic parser answers most of them — and on an
 * Ollama-configured stack that reads as a permanent malfunction.
 *
 * The chip keys off *attempted and failed*, which is the only thing a customer
 * would want to know about.
 */

function interpretation(overrides: Partial<Interpretation>): Interpretation {
  return {
    configuredProvider: "rules",
    attemptedProvider: null,
    effectiveProvider: "rules",
    fallbackReason: "rules_sufficient",
    modelName: null,
    latencyMs: null,
    ...overrides,
  };
}

describe("shouldShowFallbackChip", () => {
  it("stays hidden when the rules parser simply answered", () => {
    expect(
      shouldShowFallbackChip(
        interpretation({ configuredProvider: "rules", fallbackReason: "rules_sufficient" }),
      ),
    ).toBe(false);
  });

  it("stays hidden when Ollama is configured but rules handled a clear message", () => {
    // The case that would have produced a chip on nearly every message.
    expect(
      shouldShowFallbackChip(
        interpretation({ configuredProvider: "ollama", fallbackReason: "rules_sufficient" }),
      ),
    ).toBe(false);
  });

  it("stays hidden when no model is configured at all", () => {
    expect(
      shouldShowFallbackChip(
        interpretation({ configuredProvider: "disabled", fallbackReason: "not_configured" }),
      ),
    ).toBe(false);
  });

  it("shows when the model was reached and its answer was rejected", () => {
    expect(
      shouldShowFallbackChip(
        interpretation({
          configuredProvider: "ollama",
          attemptedProvider: "ollama",
          effectiveProvider: "rules",
          fallbackReason: "schema_rejected",
          modelName: "qwen3.5:2b",
          latencyMs: 4310,
        }),
      ),
    ).toBe(true);
  });

  it("shows when the model could not be reached", () => {
    expect(
      shouldShowFallbackChip(
        interpretation({
          configuredProvider: "ollama",
          attemptedProvider: "ollama",
          effectiveProvider: "rules",
          fallbackReason: "unreachable",
        }),
      ),
    ).toBe(true);
  });

  it("stays hidden when the model was reached and handled the message", () => {
    expect(
      shouldShowFallbackChip(
        interpretation({
          configuredProvider: "ollama",
          attemptedProvider: "ollama",
          effectiveProvider: "ollama",
          fallbackReason: null,
          modelName: "qwen3.5:2b",
          latencyMs: 4310,
        }),
      ),
    ).toBe(false);
  });
});
