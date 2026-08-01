"use client";

import { useState } from "react";

import { ApiRequestError, api } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PhoneInput } from "@/components/ui/PhoneInput";
import { Button, Callout, Field, Input, SavedNotice } from "@/components/ui/primitives";
import type { Customer } from "@/types/api";

/**
 * Edit, archive or delete one customer.
 *
 * The destructive actions are deliberately unequal. **Archiving** is the
 * ordinary one: it retires the record, leaves every issued proposal naming
 * them, and is reversible in one click. **Deleting** is refused by the server
 * whenever anything has been issued — the button is still offered, and the
 * refusal explains itself and points at archiving, because a disabled control
 * with no reason is worse than a clear "no".
 *
 * Delete asks twice. It is irreversible, and the second click names the person.
 */
export function CustomerEditor({
  customer,
  projectCount,
  issuedCount,
  onChanged,
  onDeleted,
}: {
  customer: Customer;
  /** Counted by the caller, which already has the list — so the dialog can
   *  name what deletion destroys rather than warning in the abstract. */
  projectCount: number;
  issuedCount: number;
  onChanged: (customer: Customer) => void;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [firstName, setFirstName] = useState(customer.firstName);
  const [lastName, setLastName] = useState(customer.lastName);
  const [email, setEmail] = useState(customer.email);
  const [phone, setPhone] = useState(customer.phone ?? "");
  const [company, setCompany] = useState(customer.companyName ?? "");
  const [address, setAddress] = useState(customer.address ?? "");

  function fail(caught: unknown, fallback: string) {
    setError(caught instanceof ApiRequestError ? caught.message : fallback);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const { customer: updated } = await api.updateCustomer(
        customer.customerId,
        {
          firstName,
          lastName,
          email,
          phone: phone || null,
          companyName: company || null,
          address: address || null,
        },
      );
      onChanged(updated);
      setEditing(false);
      // Confirmed rather than merely "the form closed". Closing could equally
      // mean it was cancelled, and a save with no acknowledgement is a save the
      // operator has to go and verify.
      setSaved(true);
      setTimeout(() => setSaved(false), 4000);
    } catch (caught) {
      fail(caught, "Could not save the changes.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleArchive() {
    setBusy(true);
    setError(null);
    try {
      const { customer: updated } = customer.archivedAt
        ? await api.unarchiveCustomer(customer.customerId)
        : await api.archiveCustomer(customer.customerId);
      onChanged(updated);
    } catch (caught) {
      fail(caught, "Could not change the archive state.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteCustomer(customer.customerId);
      onDeleted();
    } catch (caught) {
      fail(caught, "Could not delete this customer.");
      setConfirmingDelete(false);
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-col gap-2">
        {error ? (
          <Callout tone="warning" testId="customer-action-error">
            {error}
          </Callout>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => setEditing(true)}
            testId="edit-customer"
          >
            Edit details
          </Button>
          {saved ? <SavedNotice testId="customer-saved">Details saved</SavedNotice> : null}
          <Button
            variant="secondary"
            onClick={() => void toggleArchive()}
            disabled={busy}
            testId="archive-customer"
          >
            {customer.archivedAt ? "Restore" : "Archive"}
          </Button>
          <Button
            variant="danger-outline"
            onClick={() => {
              setError(null);
              setConfirmingDelete(true);
            }}
            testId="delete-customer"
          >
            Delete customer
          </Button>
        </div>

        <ConfirmDialog
          open={confirmingDelete}
          title={`Delete ${customer.displayName}?`}
          confirmLabel="Delete permanently"
          busy={busy}
          onCancel={() => setConfirmingDelete(false)}
          onConfirm={() => void remove()}
          testId="delete-customer-dialog"
        >
          <p>
            This deletes the customer{" "}
            <strong>and everything built for them</strong>:
          </p>
          <ul className="mt-1.5 list-disc pl-5">
            <li data-testid="delete-projects-count">
              {projectCount === 1 ? "1 project" : `${projectCount} projects`},
              with their conversations and analyses
            </li>
            <li data-testid="delete-proposals-count">
              {issuedCount === 1
                ? "1 issued proposal"
                : `${issuedCount} issued proposals`}
              {issuedCount > 0 ? (
                <>
                  {" — "}
                  <strong>
                    their share links will stop working for anyone holding them
                  </strong>
                </>
              ) : null}
            </li>
          </ul>
          <p className="mt-2">
            <strong>Archive</strong> instead if you only want them out of the
            way: the record and every issued proposal stay exactly as they are.
          </p>
        </ConfirmDialog>
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy) void save();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="First name" htmlFor="edit-first-name" required>
          <Input
            id="edit-first-name"
            value={firstName}
            onChange={setFirstName}
          />
        </Field>
        <Field label="Last name" htmlFor="edit-last-name" required>
          <Input id="edit-last-name" value={lastName} onChange={setLastName} />
        </Field>
      </div>

      <Field
        label="Email"
        htmlFor="edit-email"
        required
        hint="Changing this does not alter proposals already issued — they keep the address they were sent to."
      >
        <Input id="edit-email" type="email" value={email} onChange={setEmail} />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Phone" htmlFor="edit-phone">
          <PhoneInput id="edit-phone" value={phone} onChange={setPhone} />
        </Field>
        <Field label="Company" htmlFor="edit-company">
          <Input id="edit-company" value={company} onChange={setCompany} />
        </Field>
      </div>

      <Field label="Address" htmlFor="edit-address">
        <Input id="edit-address" value={address} onChange={setAddress} />
      </Field>

      {error ? (
        <Callout tone="warning" testId="customer-edit-error">
          {error}
        </Callout>
      ) : null}

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy} testId="save-customer">
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setEditing(false);
            setError(null);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
