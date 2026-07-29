import type { FallbackReason, Interpretation } from "@/types/api";

/**
 * What the customer is told about which engine handled their message.
 *
 * Almost nothing, almost always — and that is the design, not an omission.
 * The deterministic parser handles most messages on any stack, so a chip keyed
 * off "the configured provider was not the effective one" would appear on
 * nearly every reply and read as a permanent malfunction.
 *
 * The chip means one thing: *a model was reached and could not deliver*.
 * Everything else — which provider, which model, how long it took — lives in
 * the expandable detail, and the full record is in the API response, the
 * message payload and the server log regardless of what the UI shows.
 */

/** Reasons that mean nothing went wrong. */
const BENIGN: ReadonlySet<FallbackReason> = new Set<FallbackReason>([
  "rules_sufficient",
  "not_configured",
]);

export function shouldShowFallbackChip(
  interpretation: Interpretation | null | undefined,
): boolean {
  if (!interpretation) return false;
  // `attemptedProvider` is non-null only when an HTTP call was actually
  // issued. No call, no failure, no chip.
  if (interpretation.attemptedProvider === null) return false;
  return interpretation.effectiveProvider !== interpretation.attemptedProvider;
}

export const FALLBACK_CHIP_LABEL = "Handled with safe fallback";

/** Plain-language reason, for the detail surface rather than the bubble. */
export function describeFallback(reason: FallbackReason | null): string {
  switch (reason) {
    case "rules_sufficient":
      return "The deterministic parser understood the message; no model was needed.";
    case "not_configured":
      return "No language model is configured for this deployment.";
    case "unreachable":
      return "The model could not be reached.";
    case "timeout":
      return "The model did not answer in time.";
    case "http_error":
      return "The model service returned an error.";
    case "empty_response":
      return "The model returned nothing.";
    case "invalid_json":
      return "The model's reply was not valid JSON.";
    case "schema_rejected":
      return "The model's reply did not match the allowed shape.";
    case "domain_rejected":
      return "The model's reply was not usable for this step.";
    default:
      return "The model handled this message.";
  }
}

export function isBenign(reason: FallbackReason | null): boolean {
  return reason === null || BENIGN.has(reason);
}
