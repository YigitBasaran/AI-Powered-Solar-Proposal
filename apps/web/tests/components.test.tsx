import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel, ProgressRail } from "@/components/chat/ChatPanel";
import { SourceBadge } from "@/components/ui/primitives";
import type { ChatMessage, ProgressStep } from "@/types/api";

const steps: ProgressStep[] = [
  { step: "location", label: "Location", state: "done" },
  { step: "consumption", label: "Usage", state: "active" },
  { step: "system_size", label: "System", state: "pending" },
];

function message(role: "user" | "assistant", content: string): ChatMessage {
  return { role, content, step: null, parserSource: null, createdAt: new Date().toISOString() };
}

describe("ProgressRail", () => {
  it("renders every step", () => {
    render(<ProgressRail steps={steps} />);
    expect(screen.getByText("Location")).toBeInTheDocument();
    expect(screen.getByText("Usage")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("marks the active step for assistive technology", () => {
    render(<ProgressRail steps={steps} />);
    expect(screen.getByText("Usage").closest("li")).toHaveAttribute("aria-current", "step");
  });
});

describe("ChatPanel", () => {
  it("shows the conversation", () => {
    render(
      <ChatPanel
        messages={[message("assistant", "Welcome to solarVis AI."), message("user", "1,150 kWh")]}
        onSend={vi.fn()}
        pending={false}
      />,
    );
    expect(screen.getByText("Welcome to solarVis AI.")).toBeInTheDocument();
    expect(screen.getByText("1,150 kWh")).toBeInTheDocument();
  });

  it("sends on Enter and clears the box", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatPanel messages={[]} onSend={onSend} pending={false} />);

    const input = screen.getByLabelText("Message");
    await user.type(input, "the middle option{Enter}");

    expect(onSend).toHaveBeenCalledWith("the middle option");
    expect(input).toHaveValue("");
  });

  it("does not send an empty or whitespace-only message", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatPanel messages={[]} onSend={onSend} pending={false} />);

    await user.type(screen.getByLabelText("Message"), "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("blocks input while a request is in flight", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatPanel messages={[]} onSend={onSend} pending />);

    await user.type(screen.getByLabelText("Message"), "hello{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });
});

describe("SourceBadge", () => {
  it("carries its meaning in text, not colour alone", () => {
    render(<SourceBadge tone="fixture" label="Demo fixture" />);
    // A colourblind or monochrome reader must still see that this is not live.
    expect(screen.getByText("Demo fixture")).toBeInTheDocument();
  });
});
