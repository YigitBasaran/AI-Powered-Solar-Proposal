"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { ApiRequestError } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { cn } from "@/components/ui/primitives";

/**
 * The delete control on a list row.
 *
 * Icon-only for space, but never *only* an icon to assistive technology: it
 * carries an accessible name naming the thing it deletes, so a screen reader
 * announces "Delete Anna Schmidt" rather than "button".
 *
 * The confirmation is the same dialog the detail pages use. A list is where a
 * mis-click is most likely — the rows are small and adjacent — so it gets more
 * protection, not less.
 */
export function DeleteRowButton({
  label,
  title,
  confirmLabel = "Delete permanently",
  onDelete,
  onDeleted,
  children,
  testId,
}: {
  /** Accessible name, e.g. "Delete Anna Schmidt". */
  label: string;
  title: string;
  confirmLabel?: string;
  onDelete: () => Promise<unknown>;
  onDeleted: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await onDelete();
      setOpen(false);
      onDeleted();
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not delete this.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        aria-label={label}
        title={label}
        data-testid={testId}
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
        className={cn(
          "shrink-0 rounded-lg border border-negative/40 p-1.5 text-negative",
          "hover:bg-negative-soft",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
        )}
      >
        <Trash2 className="size-3.5" aria-hidden />
      </button>

      {/* Mounted only while it is needed. A list of fifty rows would otherwise
          carry fifty hidden dialogs, and every `dialog-cancel` selector in a
          test would match all of them. */}
      {open ? (
        <ConfirmDialog
          open
          title={title}
          confirmLabel={confirmLabel}
          busy={busy}
          onCancel={() => setOpen(false)}
          onConfirm={() => void run()}
          testId={testId ? `${testId}-dialog` : undefined}
        >
          {children}
          {error ? (
            <p
              className="mt-2 font-medium text-negative"
              data-testid="row-delete-error"
            >
              {error}
            </p>
          ) : null}
        </ConfirmDialog>
      ) : null}
    </>
  );
}
