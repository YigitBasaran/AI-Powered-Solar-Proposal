import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "@/components/chat/ChatPanel";
import type { ChatMessage, Interpretation } from "@/types/api";

/**
 * What the bubble shows about who answered.
 *
 * `shouldShowFallbackChip` is tested on its own in `telemetry.test.ts`; this
 * checks the rendering decisions built on it — that a clean answer carries no
 * warning, that a real fallback does, and that the provider details are behind
 * a disclosure rather than in the bubble.
 */

function interpretation(overrides: Partial<Interpretation> = {}): Interpretation {
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

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    role: "assistant",
    content: "Simple payback is the capital cost divided by the annual saving.",
    step: "proposal",
    parserSource: "rules",
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

function panel(message: ChatMessage) {
  return render(<ChatPanel messages={[message]} onSend={vi.fn()} pending={false} />);
}

describe("message telemetry", () => {
  it("shows nothing at all on a message with no interpretation", () => {
    panel(assistant());
    expect(screen.queryByTestId("message-telemetry")).not.toBeInTheDocument();
  });

  it("shows no warning when the rules parser simply answered", () => {
    panel(assistant({ interpretation: interpretation() }));
    expect(screen.queryByTestId("fallback-chip")).not.toBeInTheDocument();
    expect(screen.getByText("How this was read")).toBeInTheDocument();
  });

  it("shows no warning on an Ollama stack when rules handled the message", () => {
    // The case that would have chipped nearly every reply.
    panel(
      assistant({
        interpretation: interpretation({ configuredProvider: "ollama" }),
      }),
    );
    expect(screen.queryByTestId("fallback-chip")).not.toBeInTheDocument();
  });

  it("shows the chip when the model was reached and did not deliver", () => {
    panel(
      assistant({
        interpretation: interpretation({
          configuredProvider: "ollama",
          attemptedProvider: "ollama",
          effectiveProvider: "rules",
          fallbackReason: "schema_rejected",
          modelName: "qwen3.5:2b",
          latencyMs: 4310,
        }),
      }),
    );
    expect(screen.getByTestId("fallback-chip")).toHaveTextContent("Handled with safe fallback");
  });

  it("keeps the provider details out of the bubble and in the disclosure", () => {
    panel(
      assistant({
        interpretation: interpretation({
          configuredProvider: "ollama",
          attemptedProvider: "ollama",
          effectiveProvider: "ollama",
          fallbackReason: null,
          modelName: "qwen3.5:2b",
          latencyMs: 4310,
        }),
      }),
    );

    // The bubble text is the answer, not the plumbing.
    expect(screen.getByTestId("message-telemetry").tagName).toBe("DETAILS");
    expect(screen.getByTestId("message-telemetry")).not.toHaveAttribute("open");

    // …but everything an operator needs is there when opened.
    expect(screen.getByTestId("configured-provider")).toHaveTextContent("ollama");
    expect(screen.getByTestId("effective-provider")).toHaveTextContent("ollama");
    expect(screen.getByText("qwen3.5:2b")).toBeInTheDocument();
    expect(screen.getByText("4310 ms")).toBeInTheDocument();
  });

  it("says 'none' rather than blank when no model was attempted", () => {
    panel(assistant({ interpretation: interpretation({ configuredProvider: "ollama" }) }));
    expect(screen.getByTestId("attempted-provider")).toHaveTextContent("none");
  });

  it("never attaches telemetry to what the customer typed", () => {
    render(
      <ChatPanel
        messages={[
          {
            role: "user",
            content: "1,150 kWh",
            step: null,
            parserSource: null,
            createdAt: new Date().toISOString(),
            interpretation: interpretation(),
          },
        ]}
        onSend={vi.fn()}
        pending={false}
      />,
    );
    expect(screen.queryByTestId("message-telemetry")).not.toBeInTheDocument();
  });
});
