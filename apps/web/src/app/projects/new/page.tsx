"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";

import { CustomerForm } from "@/components/customers/CustomerForm";
import { ApiRequestError, api } from "@/lib/api";
import {
  Button,
  Callout,
  Card,
  Field,
  Input,
  SectionTitle,
  Spinner,
  cn,
} from "@/components/ui/primitives";
import type { Customer } from "@/types/api";

/**
 * Start a project, for somebody.
 *
 * A project exists to produce a proposal, and a proposal has to be addressed
 * to a person — so the customer is chosen *first*, here, rather than bolted on
 * afterwards. That is why this page exists instead of a bare "New project"
 * button: the workspace used to create an unattached project on load, and the
 * only way to discover it had nobody was to reach the send step and be refused.
 *
 * Creating a new customer sits beside the picker rather than on another
 * screen, collapsed by default: most projects are for someone already on file,
 * and the occasional new name should not cost a navigation and a way back.
 */
export default function NewProjectPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Customer | null>(null);
  const [name, setName] = useState("");
  const [addingCustomer, setAddingCustomer] = useState(false);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (search: string) => {
    setLoading(true);
    try {
      const body = await api.listCustomers({ q: search || undefined, pageSize: 25 });
      setCustomers(body.customers);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not reach the server. Check your connection and try again.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(query), 200);
    return () => clearTimeout(timer);
  }, [query, load]);

  async function start() {
    if (!selected) return;
    setStarting(true);
    setError(null);
    try {
      const created = await api.createProject({
        customerId: selected.customerId,
        name: name.trim() || undefined,
      });
      window.location.href = `/?project=${created.projectId}`;
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError ? caught.message : "Could not start the project.",
      );
      setStarting(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 p-4 sm:p-6">
      <div>
        <a
          href="/projects"
          data-testid="back-to-projects"
          className={cn(
            "mb-2 inline-flex items-center gap-1.5 rounded-lg border border-slate-line",
            "bg-surface px-2.5 py-1 text-[12px] text-slate-body hover:bg-surface-2",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
          )}
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          Return to all projects
        </a>
        <h1 className="text-lg font-semibold tracking-tight text-slate-ink">New project</h1>
        <p className="mt-1 text-[12.5px] text-slate-muted">
          Every project belongs to a customer, so the proposal it produces has somebody to be
          addressed to.
        </p>
      </div>

      {error ? (
        <Callout tone="warning" testId="new-project-error">
          {error}
        </Callout>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card className="p-4">
          <SectionTitle>1 · Choose a customer</SectionTitle>

          <div className="mb-3">
            <label htmlFor="pick-customer-search" className="sr-only">
              Search customers
            </label>
            <Input
              id="pick-customer-search"
              type="search"
              value={query}
              onChange={setQuery}
              placeholder="Search by name, email or company"
            />
          </div>

          {loading && customers.length === 0 ? (
            <p className="flex items-center gap-2 text-[12.5px] text-slate-muted">
              <Spinner /> Loading…
            </p>
          ) : null}

          {!loading && customers.length === 0 ? (
            <p className="text-[12.5px] text-slate-muted" data-testid="picker-empty">
              {query
                ? `No customer matches "${query}".`
                : "No customers yet — add one on the right."}
            </p>
          ) : null}

          {customers.length > 0 ? (
            <ul className="max-h-72 divide-y divide-slate-line overflow-y-auto" data-testid="customer-picker">
              {customers.map((customer) => {
                const active = selected?.customerId === customer.customerId;
                return (
                  <li key={customer.customerId}>
                    <button
                      type="button"
                      aria-pressed={active}
                      data-testid={`pick-${customer.customerId}`}
                      onClick={() => setSelected(customer)}
                      className={cn(
                        "flex w-full flex-col items-start gap-0.5 px-2 py-2 text-left",
                        "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-navy-700",
                        active ? "bg-[#eef4fa]" : "hover:bg-surface-2",
                      )}
                    >
                      <span className="text-[13px] font-medium text-slate-ink">
                        {customer.displayName}
                      </span>
                      <span className="text-[11.5px] text-slate-muted">{customer.email}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </Card>

        <Card className="p-4">
          <SectionTitle>Add a new customer</SectionTitle>

          {/* Collapsed by default: most projects are for someone already on
              file, and an open form would push the picker off the screen. */}
          <button
            type="button"
            aria-expanded={addingCustomer}
            aria-controls="new-customer-panel"
            data-testid="toggle-add-customer"
            onClick={() => setAddingCustomer((open) => !open)}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-lg border border-slate-line px-2.5 py-1.5",
              "text-[13px] text-slate-body hover:bg-surface-2",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
            )}
          >
            {addingCustomer ? (
              <ChevronDown className="size-4" aria-hidden />
            ) : (
              <ChevronRight className="size-4" aria-hidden />
            )}
            {addingCustomer ? "Close" : "Add a customer who is not on the list"}
          </button>

          {addingCustomer ? (
            <div id="new-customer-panel" className="mt-3">
              <CustomerForm
                onCreated={(customer) => {
                  // Created *and* selected: creating someone in order to start
                  // a project for them and then having to find them in the list
                  // is a step that exists only because the form forgot.
                  setCustomers((previous) => [customer, ...previous]);
                  setSelected(customer);
                  setAddingCustomer(false);
                }}
                onUseExisting={(customerId) => {
                  setQuery("");
                  void load("").then(() => {
                    setCustomers((rows) => {
                      const match = rows.find((row) => row.customerId === customerId);
                      if (match) setSelected(match);
                      return rows;
                    });
                  });
                  setAddingCustomer(false);
                }}
              />
            </div>
          ) : null}
        </Card>
      </div>

      <Card className="p-4">
        <SectionTitle>2 · Name it and start</SectionTitle>

        <div className="mb-3 text-[12.5px]" data-testid="picked-customer">
          {selected ? (
            <>
              For <strong>{selected.displayName}</strong>{" "}
              <span className="text-slate-muted">({selected.email})</span>
            </>
          ) : (
            <span className="text-slate-muted">Choose a customer above to continue.</span>
          )}
        </div>

        <Field
          label="Project name"
          htmlFor="new-project-name"
          hint="Optional. Left blank, it is named after the customer and today's date."
        >
          <Input
            id="new-project-name"
            value={name}
            onChange={setName}
            placeholder={selected ? `${selected.displayName} — roof` : "Roof, phase 1"}
          />
        </Field>

        <div className="mt-3">
          <Button
            onClick={() => void start()}
            disabled={!selected || starting}
            testId="start-project"
          >
            {starting ? "Starting…" : "Start project"}
          </Button>
        </div>
      </Card>
    </main>
  );
}
