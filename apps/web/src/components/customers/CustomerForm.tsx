"use client";

import { useState } from "react";

import { ApiRequestError, api } from "@/lib/api";
import { PhoneInput } from "@/components/ui/PhoneInput";
import { Button, Callout, Field, Input } from "@/components/ui/primitives";
import type { Customer } from "@/types/api";

/**
 * Create a customer.
 *
 * Client-side validation is deliberately thin: it catches the empty field and
 * the obviously-not-an-address, and leaves everything else to the server. The
 * server is the only place that knows whether an address is already taken, and
 * duplicating its email rule here would give two answers that drift apart.
 *
 * The duplicate case is the one worth handling well. The API returns the id of
 * the record that collided, so this offers to use that customer rather than
 * leaving the operator at a dead end with a form they cannot submit.
 */
export function CustomerForm({
  onCreated,
  onUseExisting,
}: {
  onCreated: (customer: Customer) => void;
  onUseExisting?: (customerId: string) => void;
}) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [company, setCompany] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duplicateId, setDuplicateId] = useState<string | null>(null);

  const missing = !firstName.trim() || !lastName.trim() || !email.trim();

  async function submit() {
    setBusy(true);
    setError(null);
    setDuplicateId(null);
    try {
      const { customer } = await api.createCustomer({
        firstName,
        lastName,
        email,
        phone: phone || null,
        companyName: company || null,
      });
      onCreated(customer);
      setFirstName("");
      setLastName("");
      setEmail("");
      setPhone("");
      setCompany("");
    } catch (caught) {
      if (caught instanceof ApiRequestError) {
        setError(caught.message);
        const details = caught.details as { customerId?: string } | undefined;
        if (caught.code === "CUSTOMER_EMAIL_EXISTS" && details?.customerId) {
          setDuplicateId(details.customerId);
        }
      } else {
        setError(
          "Could not reach the server. Check your connection and try again.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!missing && !busy) void submit();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="First name" htmlFor="customer-first-name" required>
          <Input
            id="customer-first-name"
            value={firstName}
            onChange={setFirstName}
            autoComplete="given-name"
          />
        </Field>
        <Field label="Last name" htmlFor="customer-last-name" required>
          <Input
            id="customer-last-name"
            value={lastName}
            onChange={setLastName}
            autoComplete="family-name"
          />
        </Field>
      </div>

      <Field
        label="Email"
        htmlFor="customer-email"
        required
        hint="Where the proposal will be sent. Each customer has one address."
        error={duplicateId ? null : undefined}
      >
        <Input
          id="customer-email"
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          invalid={Boolean(duplicateId)}
        />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Phone" htmlFor="customer-phone">
          <PhoneInput id="customer-phone" value={phone} onChange={setPhone} />
        </Field>
        <Field label="Company" htmlFor="customer-company">
          <Input id="customer-company" value={company} onChange={setCompany} />
        </Field>
      </div>

      {error ? (
        <Callout tone="warning" testId="customer-form-error">
          {error}
          {duplicateId && onUseExisting ? (
            <>
              {" "}
              <button
                type="button"
                className="underline underline-offset-2"
                onClick={() => onUseExisting(duplicateId)}
                data-testid="use-existing-customer"
              >
                Use that customer instead
              </button>
            </>
          ) : null}
        </Callout>
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          type="submit"
          disabled={missing || busy}
          testId="create-customer"
        >
          {busy ? "Creating…" : "Create customer"}
        </Button>
        {busy ? (
          <span role="status" className="text-[12px] text-slate-muted">
            Creating…
          </span>
        ) : null}
      </div>
    </form>
  );
}
