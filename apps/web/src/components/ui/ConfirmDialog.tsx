"use client";

import { useEffect, useRef } from "react";

import { Button, Callout, cn } from "@/components/ui/primitives";

/**
 * A modal confirmation for irreversible actions.
 *
 * The first modal in this application, and it exists because deletion here can
 * destroy a document a customer is holding a link to. An inline two-step
 * button was enough for *sending* — that is recoverable — but not for this.
 *
 * The accessibility parts are not decoration. A dialog that does not trap
 * focus, does not close on Escape and does not name itself to a screen reader
 * is a visual overlay, not a dialog: a keyboard user can tab straight past it
 * into the page it is supposedly blocking and press the very control it was
 * asking about.
 *
 * `<dialog showModal>` gives focus trapping, the inert backdrop and Escape
 * handling from the platform rather than from hand-written key listeners.
 */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
  children,
  testId,
}: {
  open: boolean;
  title: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  children: React.ReactNode;
  testId?: string;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      data-testid={testId}
      // Escape and the backdrop both cancel. Cancelling is always the safe
      // outcome here, so the easy gestures lead to it.
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClick={(event) => {
        if (event.target === ref.current && !busy) onCancel();
      }}
      className={cn(
        "m-auto w-[min(30rem,calc(100vw-2rem))] rounded-xl border border-slate-line bg-surface p-0",
        "text-slate-ink shadow-[0_10px_40px_rgba(11,11,11,0.18)]",
        "backdrop:bg-slate-ink/40",
      )}
    >
      {/* Stops a click inside the panel from reaching the backdrop handler. */}
      <div
        className="flex flex-col gap-3 p-4"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="text-[15px] font-semibold tracking-tight text-slate-ink">
          {title}
        </h2>

        <div className="text-[12.5px] leading-relaxed text-slate-body">
          {children}
        </div>

        <Callout tone="warning">This cannot be undone.</Callout>

        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            onClick={onCancel}
            disabled={busy}
            testId="dialog-cancel"
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={onConfirm}
            disabled={busy}
            testId="dialog-confirm"
          >
            {busy ? "Deleting…" : confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
