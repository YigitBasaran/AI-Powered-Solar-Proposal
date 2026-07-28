"use client";

import { Loader2, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/components/ui/primitives";
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
            step.state === "done" && "border-good-600/30 bg-[#e8f6ec] text-good-700",
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
