"use client";

import { AlertTriangle, Loader2, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/components/ui/primitives";
import {
  FALLBACK_CHIP_LABEL,
  describeFallback,
  shouldShowFallbackChip,
} from "@/lib/telemetry";
import type { ChatMessage, ProgressStep } from "@/types/api";

export function ProgressRail({ steps }: { steps: ProgressStep[] }) {
  return (
    <ol className="flex flex-wrap gap-1.5" aria-label="Progress">
      {steps.map((step) => (
        <li
          key={step.step}
          aria-current={step.state === "active" ? "step" : undefined}
          className={cn(
            "rounded-full border px-2 py-0.5 text-[11px] font-medium",
            step.state === "done" &&
              "border-good-600/30 bg-[#e8f6ec] text-good-700",
            step.state === "active" && "border-navy-700 bg-navy-900 text-white",
            step.state === "pending" && "border-slate-line text-slate-muted",
          )}
        >
          {step.label}
        </li>
      ))}
    </ol>
  );
}

/**
 * Provenance, shown sparingly.
 *
 * The chip appears only when a model was genuinely reached and did not
 * deliver — never merely because a model is configured. Everything else (which
 * provider, which model, how long) sits behind a disclosure, because a customer
 * asking "why did it answer like that?" is a rare and deliberate act, and model
 * names on every bubble are noise dressed as transparency.
 */
function MessageTelemetry({ message }: { message: ChatMessage }) {
  const interpretation = message.interpretation;
  if (!interpretation) return null;

  return (
    <details
      className="mt-1.5 text-[11px] text-slate-muted"
      data-testid="message-telemetry"
    >
      <summary className="cursor-pointer list-none">
        {shouldShowFallbackChip(interpretation) ? (
          // Colour, an icon and the words together: the state has to survive a
          // greyscale print and a colour-blind reader, and amber text on amber
          // at 11px would not carry 4.5:1 on its own.
          <span
            data-testid="fallback-chip"
            className="inline-flex items-center gap-1 rounded-full border border-solar-600/40 bg-solar-100 px-2 py-0.5 font-medium text-slate-ink"
          >
            <AlertTriangle className="size-3" aria-hidden />
            {FALLBACK_CHIP_LABEL}
          </span>
        ) : (
          <span className="underline decoration-dotted underline-offset-2">
            How this was read
          </span>
        )}
      </summary>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        <dt>Configured</dt>
        <dd data-testid="configured-provider">
          {interpretation.configuredProvider}
        </dd>
        <dt>Attempted</dt>
        <dd data-testid="attempted-provider">
          {interpretation.attemptedProvider ?? "none"}
        </dd>
        <dt>Effective</dt>
        <dd data-testid="effective-provider">
          {interpretation.effectiveProvider}
        </dd>
        <dt>Reason</dt>
        <dd data-testid="fallback-reason">
          {describeFallback(interpretation.fallbackReason)}
        </dd>
        {interpretation.modelName ? (
          <>
            <dt>Model</dt>
            <dd>{interpretation.modelName}</dd>
          </>
        ) : null}
        {interpretation.latencyMs !== null ? (
          <>
            <dt>Latency</dt>
            <dd className="tnum">{interpretation.latencyMs} ms</dd>
          </>
        ) : null}
      </dl>
    </details>
  );
}

export function ChatPanel({
  messages,
  onSend,
  pending,
  disabled,
  placeholder = "Type your answer…",
}: {
  messages: ChatMessage[];
  onSend: (message: string) => void;
  pending: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.length, pending]);

  function submit() {
    const trimmed = draft.trim();
    if (!trimmed || pending || disabled) return;
    onSend(trimmed);
    setDraft("");
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* A scrollable region needs its own tab stop, or a keyboard user can
          reach the composer but never scroll back through the conversation.
          `role="log"` also has assistive technology announce new replies. */}
      <div
        ref={scrollRef}
        tabIndex={0}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-navy-700"
      >
        {messages.map((message, index) => (
          <div
            key={`${message.createdAt}-${index}`}
            className={cn(
              "animate-rise flex",
              message.role === "user" ? "justify-end" : "justify-start",
            )}
          >
            <div
              className={cn(
                "max-w-[88%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-[13px] leading-relaxed",
                message.role === "user"
                  ? "rounded-br-sm bg-navy-900 text-white"
                  : "rounded-bl-sm border border-slate-line bg-surface text-slate-ink",
              )}
            >
              {message.content}
              {message.role === "assistant" ? (
                <MessageTelemetry message={message} />
              ) : null}
            </div>
          </div>
        ))}

        {pending ? (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-2xl rounded-bl-sm border border-slate-line bg-surface px-3 py-2 text-[12.5px] text-slate-muted">
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              Thinking…
            </div>
          </div>
        ) : null}
      </div>

      <form
        className="flex items-end gap-2 border-t border-slate-line px-3 py-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="chat-input">
          Message
        </label>
        <textarea
          id="chat-input"
          rows={1}
          value={draft}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className={cn(
            "max-h-28 min-h-[38px] flex-1 resize-none rounded-lg border border-slate-line bg-surface px-3 py-2",
            "text-[13px] outline-none placeholder:text-slate-muted",
            "focus:border-navy-600 focus:ring-2 focus:ring-navy-600/15",
            "disabled:bg-surface-2 disabled:text-slate-muted",
          )}
        />
        <button
          type="submit"
          disabled={pending || disabled || !draft.trim()}
          aria-label="Send message"
          className={cn(
            "grid size-[38px] shrink-0 place-items-center rounded-lg bg-navy-900 text-white",
            "transition-colors hover:bg-navy-800 disabled:bg-slate-rule",
          )}
        >
          <Send className="size-4" aria-hidden />
        </button>
      </form>
    </div>
  );
}
