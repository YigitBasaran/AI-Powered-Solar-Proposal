"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiRequestError, api } from "@/lib/api";
import {
  Button,
  Callout,
  Card,
  SectionTitle,
} from "@/components/ui/primitives";
import type { Delivery, EmailPreview } from "@/types/api";

/**
 * Send a finalised proposal to its customer.
 *
 * Three rules shape this component, and all three are about not overstating
 * what happened.
 *
 * **Confirmation is two steps, in place.** There are no modals anywhere in
 * this application, so "Send…" reveals the recipient and the message, and a
 * second, differently-labelled button actually sends. The second button names
 * the recipient, so the click that sends is never ambiguous about who receives
 * it.
 *
 * **Success is never shown before the provider accepts.** The `sent` state is
 * entered from the delivery record the API returns, not from the request
 * completing.
 *
 * **Console mode says "recorded", never "sent".** The delivery carries
 * `providerSends`, so the wording is derived from what actually happened rather
 * than from what was configured when the page loaded.
 */
type Phase = "idle" | "confirming" | "sending" | "done" | "error";

/**
 * A fresh value per deliberate resend, so it computes a different send key.
 *
 * Deliberately not `crypto.randomUUID`, which is unavailable over plain HTTP on
 * a LAN address — exactly where this app gets demonstrated.
 */
function newResendNonce(): string {
  return `resend-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function SendProposalPanel({
  proposalId,
  shareToken,
  publicUrl,
}: {
  proposalId: string;
  shareToken: string;
  publicUrl: string;
}) {
  const [preview, setPreview] = useState<EmailPreview | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  //: Non-null only while confirming a deliberate resend. See `beginConfirm`.
  const [resendNonce, setResendNonce] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [{ preview: rendered }, { deliveries: history }] =
        await Promise.all([
          api.emailPreview(proposalId),
          api.deliveries(proposalId),
        ]);
      setPreview(rendered);
      setDeliveries(history);
      setLoadError(null);
    } catch (caught) {
      setLoadError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not load the email preview.",
      );
    }
  }, [proposalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const latest = deliveries[0] ?? null;
  const alreadySent = deliveries.some((d) => d.status === "sent");

  /**
   * Opens the confirmation step, minting a nonce when this is a *resend*.
   *
   * The send key is `sha256(proposalId:recipient:revision:nonce)` and it is
   * UNIQUE, which is what turns a double-click into one email. Without a nonce
   * a second send recomputes the first send's key, finds a row already `sent`,
   * and is refused with `PROPOSAL_ALREADY_SENT` — so "Send again…" could not
   * send again at all. It is minted here rather than at the moment of sending
   * so that double-clicking *Confirm* still reuses one key: one deliberate
   * resend is one intent, however many times the button is pressed.
   */
  function beginConfirm() {
    setResendNonce(alreadySent ? newResendNonce() : null);
    setPhase("confirming");
  }

  async function send() {
    setPhase("sending");
    setError(null);
    try {
      const { delivery } = await api.sendProposal(proposalId, {
        resendNonce: resendNonce ?? undefined,
      });
      setDeliveries((previous) => [delivery, ...previous]);
      setPhase("done");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not reach the server, so it is not known whether the proposal was sent.",
      );
      setPhase("error");
      void load();
    }
  }

  async function retry(deliveryId: string) {
    setPhase("sending");
    setError(null);
    try {
      const { delivery } = await api.retryDelivery(proposalId, deliveryId);
      setDeliveries((previous) => [
        delivery,
        ...previous.filter((d) => d.deliveryId !== deliveryId),
      ]);
      setPhase("done");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "The retry failed.",
      );
      setPhase("error");
      void load();
    }
  }

  // The client disables exactly what the server would refuse, and says why -
  // a mute disabled button leaves the operator guessing.
  const blocked = !preview
    ? "Loading…"
    : !preview.to
      ? "This proposal has no customer, so there is nobody to email it to."
      : !preview.providerAvailable
        ? (preview.providerDetail ?? "Email is not configured.")
        : null;

  return (
    <Card className="p-4">
      <SectionTitle>Send to customer</SectionTitle>

      {loadError ? <Callout tone="warning">{loadError}</Callout> : null}

      {latest ? (
        <DeliveryState
          delivery={latest}
          onRetry={retry}
          busy={phase === "sending"}
        />
      ) : null}

      {blocked ? (
        <p
          className="text-[12.5px] text-slate-muted"
          data-testid="send-blocked-reason"
        >
          {blocked}
        </p>
      ) : null}

      {preview && !blocked ? (
        <div className="mt-3 flex flex-col gap-3">
          {phase === "idle" ? (
            <Button
              onClick={beginConfirm}
              testId="send-proposal"
              variant={alreadySent ? "secondary" : "primary"}
            >
              {alreadySent ? "Send again…" : "Send proposal…"}
            </Button>
          ) : null}

          {phase === "confirming" ? (
            <>
              <dl className="rounded-lg border border-slate-line bg-surface-2 p-3 text-[12.5px]">
                <Pair
                  label="To"
                  value={preview.to ?? ""}
                  testId="preview-recipient"
                />
                <Pair
                  label="Subject"
                  value={preview.subject ?? ""}
                  testId="preview-subject"
                />
                <Pair label="Revision" value={`${preview.revisionNumber}`} />
                <Pair label="Link" value={preview.publicUrl} />
                <Pair
                  label="Attachment"
                  value={
                    preview.includesPdf
                      ? "PDF attached"
                      : "Link only, no attachment"
                  }
                />
              </dl>

              <pre
                className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-line bg-surface p-3 text-[12px] leading-relaxed text-slate-body"
                data-testid="preview-body"
              >
                {preview.textBody}
              </pre>

              {resendNonce ? (
                <Callout tone="info" testId="resend-notice">
                  This proposal has already been sent to this address. Confirming
                  sends a <strong>second copy</strong> of the same revision — it
                  does not replace the first.
                </Callout>
              ) : null}

              {!preview.providerSends ? (
                <Callout tone="info" testId="console-mode-notice">
                  Console mode: this will be recorded locally and{" "}
                  <strong>not</strong> sent to the customer. Configure SMTP to
                  deliver it for real.
                </Callout>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void send()} testId="confirm-send">
                  {preview.providerSends
                    ? `Confirm and send to ${preview.to}`
                    : `Confirm and record for ${preview.to}`}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setPhase("idle")}
                  testId="cancel-send"
                >
                  Cancel
                </Button>
              </div>
            </>
          ) : null}

          {phase === "sending" ? (
            <p
              role="status"
              className="text-[12.5px] text-slate-muted"
              data-testid="send-status"
            >
              Sending…
            </p>
          ) : null}

          {phase === "error" && error ? (
            <Callout tone="warning" testId="send-error">
              {error} The proposal itself is unaffected and its link still works
              — you can <CopyLink url={publicUrl} /> and send it yourself.
            </Callout>
          ) : null}

          {phase === "done" ? (
            <Button variant="ghost" onClick={() => setPhase("idle")} testId="send-done">
              Done
            </Button>
          ) : null}
        </div>
      ) : null}

      <p className="mt-3 text-[11px] text-slate-muted">
        Share token <code>{shareToken.slice(0, 8)}…</code>
      </p>
    </Card>
  );
}

function DeliveryState({
  delivery,
  onRetry,
  busy,
}: {
  delivery: Delivery;
  onRetry: (deliveryId: string) => void;
  busy: boolean;
}) {
  // The wording is derived from the record, not from configuration - so it
  // cannot claim a send that console mode never made.
  const label =
    delivery.status === "sent"
      ? delivery.providerSends
        ? "Sent"
        : "Recorded locally (console mode) — not sent"
      : delivery.status === "failed"
        ? "Failed"
        : delivery.status === "sending"
          ? "Sending… (may still be in flight)"
          : "Not sent yet";

  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px]"
      data-testid="delivery-state"
      data-status={delivery.status}
      data-provider-sends={delivery.providerSends}
    >
      <strong className="font-semibold text-slate-ink">{label}</strong>
      {delivery.recipientMasked ? (
        <span className="text-slate-muted">{delivery.recipientMasked}</span>
      ) : null}
      {delivery.sentAt ? (
        <span className="text-slate-muted">
          {new Date(delivery.sentAt).toLocaleString()}
        </span>
      ) : null}
      {delivery.status === "failed" ? (
        <>
          <span className="text-slate-muted">{delivery.errorMessage}</span>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => onRetry(delivery.deliveryId)}
            testId="retry-delivery"
          >
            Retry
          </Button>
        </>
      ) : null}
    </div>
  );
}

function Pair({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div className="flex gap-3 py-0.5">
      <dt className="w-20 shrink-0 text-slate-muted">{label}</dt>
      <dd className="min-w-0 break-words text-slate-ink" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

function CopyLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="underline underline-offset-2"
      data-testid="copy-public-link"
      onClick={() => {
        void navigator.clipboard?.writeText(url);
        setCopied(true);
      }}
    >
      {copied ? "copied the link" : "copy the link"}
    </button>
  );
}
