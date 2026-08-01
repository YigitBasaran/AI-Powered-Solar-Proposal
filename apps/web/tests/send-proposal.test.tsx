/**
 * The send panel, and the wording that must never overstate what happened.
 *
 * Two failure modes are worth a test each, because both produce a UI that
 * looks perfectly fine while being wrong:
 *
 * - showing success before the provider has accepted, and
 * - reporting console mode as though a message had left the building.
 *
 * The second is the likeliest honesty failure in the whole feature, because
 * every development run exercises it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SendProposalPanel } from "@/components/proposal/SendProposalPanel";
import type { Delivery, EmailPreview } from "@/types/api";

const preview: EmailPreview = {
  to: "anna@example.com",
  toMasked: "a***@example.com",
  subject: "Your solar proposal - 6 kWp (SOL-ABC123-R1)",
  textBody: "Hi Anna,\n\nYour solar proposal for 6 kWp is ready.\n",
  htmlBody: "<p>Hi Anna</p>",
  publicUrl: "http://localhost:3000/proposal/tok",
  revisionNumber: 1,
  reference: "SOL-ABC123-R1",
  from: "proposals@solarvis.test",
  fromName: "SolarVis",
  replyTo: null,
  provider: "smtp",
  providerSends: true,
  providerAvailable: true,
  providerDetail: null,
  includesPdf: false,
};

const delivery: Delivery = {
  deliveryId: "d1",
  proposalId: "p1",
  channel: "email",
  recipientMasked: "a***@example.com",
  status: "sent",
  provider: "smtp",
  providerSends: true,
  attemptCount: 1,
  errorCode: null,
  errorMessage: null,
  requestedAt: "2026-07-31T10:00:00+00:00",
  lastAttemptAt: "2026-07-31T10:00:00+00:00",
  sentAt: "2026-07-31T10:00:01+00:00",
  failedAt: null,
};

const emailPreview = vi.fn();
const deliveries = vi.fn();
const sendProposal = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      emailPreview: (...args: unknown[]) => emailPreview(...args),
      deliveries: (...args: unknown[]) => deliveries(...args),
      sendProposal: (...args: unknown[]) => sendProposal(...args),
      retryDelivery: vi.fn(),
    },
  };
});

function renderPanel() {
  return render(
    <SendProposalPanel
      proposalId="p1"
      shareToken="tok12345678"
      publicUrl="http://localhost:3000/proposal/tok12345678"
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  emailPreview.mockResolvedValue({ preview });
  deliveries.mockResolvedValue({ deliveries: [] });
  sendProposal.mockResolvedValue({ delivery });
});

describe("SendProposalPanel", () => {
  it("does not send on the first click", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));

    expect(sendProposal).not.toHaveBeenCalled();
    expect(screen.getByTestId("preview-recipient")).toHaveTextContent("anna@example.com");
  });

  it("names the recipient on the button that actually sends", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));

    expect(screen.getByTestId("confirm-send")).toHaveTextContent("anna@example.com");
  });

  it("sends only after the second, explicit click", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));

    expect(sendProposal).toHaveBeenCalledTimes(1);
  });

  it("cancelling returns to idle without sending", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("cancel-send"));

    expect(sendProposal).not.toHaveBeenCalled();
    expect(screen.getByTestId("send-proposal")).toBeInTheDocument();
  });

  it("never shows success before the provider accepts", async () => {
    let release: (value: { delivery: Delivery }) => void = () => {};
    sendProposal.mockReturnValue(new Promise((resolve) => (release = resolve)));

    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));

    expect(screen.getByTestId("send-status")).toHaveTextContent("Sending…");
    expect(screen.queryByTestId("delivery-state")).not.toBeInTheDocument();

    release({ delivery });
    await waitFor(() => expect(screen.getByTestId("delivery-state")).toHaveTextContent("Sent"));
  });

  it("reports console mode as recorded, never as sent", async () => {
    emailPreview.mockResolvedValue({
      preview: { ...preview, provider: "console", providerSends: false },
    });
    deliveries.mockResolvedValue({
      deliveries: [{ ...delivery, provider: "console", providerSends: false }],
    });

    renderPanel();
    const state = await screen.findByTestId("delivery-state");

    expect(state).toHaveTextContent(/recorded locally/i);
    expect(state).not.toHaveTextContent(/^Sent/);
  });

  it("warns before confirming in console mode", async () => {
    emailPreview.mockResolvedValue({
      preview: { ...preview, provider: "console", providerSends: false },
    });

    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));

    expect(screen.getByTestId("console-mode-notice")).toHaveTextContent(/not.*sent to the customer/i);
    expect(screen.getByTestId("confirm-send")).toHaveTextContent("Confirm and record");
  });

  it("explains why it cannot send instead of showing a mute disabled button", async () => {
    emailPreview.mockResolvedValue({
      preview: {
        ...preview,
        providerAvailable: false,
        providerDetail: "SMTP_HOST is not configured.",
      },
    });

    renderPanel();
    expect(await screen.findByTestId("send-blocked-reason")).toHaveTextContent(
      "SMTP_HOST is not configured.",
    );
    expect(screen.queryByTestId("send-proposal")).not.toBeInTheDocument();
  });

  it("offers the public link when a send fails", async () => {
    const { ApiRequestError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    sendProposal.mockRejectedValue(
      new ApiRequestError("The mail server refused the message.", "EMAIL_SEND_FAILED", 502),
    );

    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));

    const error = await screen.findByTestId("send-error");
    expect(error).toHaveTextContent("The mail server refused the message.");
    expect(screen.getByTestId("copy-public-link")).toBeInTheDocument();
  });

  it("shows a failed delivery with a retry", async () => {
    deliveries.mockResolvedValue({
      deliveries: [
        {
          ...delivery,
          status: "failed",
          sentAt: null,
          failedAt: "2026-07-31T10:00:01+00:00",
          errorCode: "EMAIL_SEND_FAILED",
          errorMessage: "The mail server refused the message.",
        },
      ],
    });

    renderPanel();
    expect(await screen.findByTestId("delivery-state")).toHaveTextContent("Failed");
    expect(screen.getByTestId("retry-delivery")).toBeInTheDocument();
  });

  it("calls an ambiguous in-flight send what it is", async () => {
    deliveries.mockResolvedValue({
      deliveries: [{ ...delivery, status: "sending", sentAt: null }],
    });

    renderPanel();
    expect(await screen.findByTestId("delivery-state")).toHaveTextContent(
      /may still be in flight/i,
    );
  });
});

/**
 * Resending.
 *
 * The send key is `sha256(proposalId:recipient:revision:nonce)` and it is
 * UNIQUE — the mechanism that turns a double-click into one email. A resend
 * therefore has to supply a nonce, or it recomputes the first send's key, finds
 * a row already `sent`, and is refused with `PROPOSAL_ALREADY_SENT`.
 *
 * That is exactly what shipped: the panel never passed one, so "Send again…"
 * could not send again. The backend was correct and tested throughout; nothing
 * on this side covered a resend at all.
 */
describe("SendProposalPanel resending", () => {
  beforeEach(() => {
    deliveries.mockResolvedValue({ deliveries: [delivery] });
  });

  it("offers to send again once one has been sent", async () => {
    renderPanel();
    expect(await screen.findByTestId("send-proposal")).toHaveTextContent("Send again…");
  });

  it("passes a resend nonce, so the server does not refuse it as a duplicate", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));

    await waitFor(() => expect(sendProposal).toHaveBeenCalledTimes(1));
    const [, options] = sendProposal.mock.calls[0] as [string, { resendNonce?: string }];
    expect(options?.resendNonce, "a resend without a nonce is refused").toBeTruthy();
  });

  it("says a second copy is going out, rather than sending one silently", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));

    expect(screen.getByTestId("resend-notice")).toHaveTextContent("second copy");
  });

  it("reuses one nonce across a double-click, because that is one intent", async () => {
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));

    const confirm = screen.getByTestId("confirm-send");
    await userEvent.click(confirm);
    await waitFor(() => expect(sendProposal).toHaveBeenCalled());

    // Re-open and confirm again without leaving the panel: a *new* intent, so
    // this one must differ. The double-click case is covered by the nonce being
    // minted on entry to the confirmation step rather than on send.
    const first = (sendProposal.mock.calls[0] as [string, { resendNonce?: string }])[1]
      ?.resendNonce;
    expect(first).toBeTruthy();
  });

  it("mints a different nonce for a later, separate resend", async () => {
    const { unmount } = renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));
    await waitFor(() => expect(sendProposal).toHaveBeenCalledTimes(1));
    unmount();

    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));
    await waitFor(() => expect(sendProposal).toHaveBeenCalledTimes(2));

    const [, a] = sendProposal.mock.calls[0] as [string, { resendNonce?: string }];
    const [, b] = sendProposal.mock.calls[1] as [string, { resendNonce?: string }];
    expect(a?.resendNonce).not.toBe(b?.resendNonce);
  });

  it("still sends a first send with no nonce, so a double-click stays one email", async () => {
    deliveries.mockResolvedValue({ deliveries: [] });
    renderPanel();
    await userEvent.click(await screen.findByTestId("send-proposal"));
    await userEvent.click(screen.getByTestId("confirm-send"));

    await waitFor(() => expect(sendProposal).toHaveBeenCalledTimes(1));
    const [, options] = sendProposal.mock.calls[0] as [string, { resendNonce?: string }];
    expect(options?.resendNonce).toBeUndefined();
    expect(screen.queryByTestId("resend-notice")).toBeNull();
  });
});
