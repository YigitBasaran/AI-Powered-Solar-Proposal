"use client";

import { useCallback, useEffect, useState } from "react";

import { CustomerForm } from "@/components/customers/CustomerForm";
import { DeleteRowButton } from "@/components/ui/DeleteRowButton";
import { Pager } from "@/components/ui/Pager";
import { ApiRequestError, api } from "@/lib/api";
import {
  Button,
  Callout,
  Card,
  DataTable,
  Input,
  SectionTitle,
  Spinner,
} from "@/components/ui/primitives";
import type { Customer } from "@/types/api";

/**
 * The customer list: a table, paged by the server.
 *
 * Search is a plain substring match. Deliberately not fuzzy — this screen picks
 * who receives an email, and a near-miss ranked first is worse than no match.
 *
 * Paging is server-driven rather than a slice of everything already fetched,
 * so "page 3 of 9" is a fact rather than an estimate and the browser never
 * holds a table it is not showing.
 */
export default function CustomersPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async (search: string, wanted: number, size: number) => {
    setLoading(true);
    try {
      const body = await api.listCustomers({ q: search || undefined, page: wanted, pageSize: size });
      setCustomers(body.customers);
      setTotal(body.total);
      setTotalPages(body.totalPages);
      setError(null);
    } catch (caught) {
      // The established retry rule: a structured API error is a real answer and
      // is never retried; only a transport failure is worth trying again.
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
    const timer = setTimeout(() => void load(query, page, pageSize), 200);
    return () => clearTimeout(timer);
  }, [query, page, pageSize, load]);

  // A new search restarts at page one; staying on page 4 of the previous
  // result set lands on an empty page of the new one.
  useEffect(() => {
    setPage(1);
  }, [query]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      setCreating(true);
    }
  }, []);

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight text-slate-ink">Customers</h1>
        <Button onClick={() => setCreating((open) => !open)} testId="toggle-new-customer">
          {creating ? "Close" : "Add New Customer"}
        </Button>
      </header>

      {creating ? (
        <Card className="p-4">
          <SectionTitle>Add a new customer</SectionTitle>
          <CustomerForm
            onCreated={(customer) => {
              setCustomers((previous) => [customer, ...previous]);
              setTotal((previous) => previous + 1);
              setCreating(false);
            }}
            onUseExisting={(customerId) => {
              window.location.href = `/customers/${customerId}`;
            }}
          />
        </Card>
      ) : null}

      <Card className="p-4">
        <div className="mb-3">
          <label htmlFor="customer-search" className="sr-only">
            Search customers
          </label>
          <Input
            id="customer-search"
            type="search"
            value={query}
            onChange={setQuery}
            placeholder="Search by name, email or company"
          />
        </div>

        {error ? (
          <Callout tone="warning" testId="customers-error">
            {error}{" "}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => void load(query, page, pageSize)}
            >
              Try again
            </button>
          </Callout>
        ) : null}

        {loading && customers.length === 0 ? (
          <p className="flex items-center gap-2 text-[12.5px] text-slate-muted">
            <Spinner /> Loading customers…
          </p>
        ) : null}

        {!loading && !error && customers.length === 0 ? (
          <p className="text-[12.5px] text-slate-muted" data-testid="customers-empty">
            {query
              ? `No customer matches "${query}".`
              : "No customers yet. Add one to start a proposal for them."}
          </p>
        ) : null}

        {customers.length > 0 ? (
          <>
            <DataTable
              label="Customers"
              headers={[
                { label: "Name" },
                { label: "Email" },
                { label: "Company" },
                { label: "Projects", align: "right" },
                { label: "Added" },
                { label: "" },
              ]}
            >
              {customers.map((customer) => (
                <tr key={customer.customerId} data-testid="customer-row">
                  <td className="px-2 py-1.5">
                    <a
                      href={`/customers/${customer.customerId}`}
                      className="text-[13px] font-medium text-navy-700 underline underline-offset-2"
                      data-testid={`customer-${customer.customerId}`}
                    >
                      {customer.displayName}
                    </a>
                    {customer.archivedAt ? (
                      <span className="ml-2 text-[11px] text-slate-muted">archived</span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5 text-[12.5px] text-slate-body">{customer.email}</td>
                  <td className="px-2 py-1.5 text-[12.5px] text-slate-muted">
                    {customer.companyName ?? "—"}
                  </td>
                  <td
                    className="px-2 py-1.5 text-right text-[12.5px]"
                    data-testid={`customer-${customer.customerId}-projects`}
                  >
                    {customer.projectCount ?? 0}
                  </td>
                  <td className="px-2 py-1.5 text-[12px] text-slate-muted">
                    {new Date(customer.createdAt).toLocaleDateString()}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <DeleteRowButton
                      label={`Delete ${customer.displayName}`}
                      title={`Delete ${customer.displayName}?`}
                      testId={`delete-customer-${customer.customerId}`}
                      onDelete={() => api.deleteCustomer(customer.customerId)}
                      onDeleted={() => void load(query, page, pageSize)}
                    >
                      <p>
                        This deletes the customer{" "}
                        <strong>and everything built for them</strong> —{" "}
                        {customer.projectCount === 1
                          ? "1 project"
                          : `${customer.projectCount ?? 0} projects`}
                        , with their conversations, analyses and any issued proposals.
                      </p>
                      <p className="mt-2">
                        Any share link already sent to them will stop working. Open their
                        record and <strong>archive</strong> instead if you only want them out
                        of the way.
                      </p>
                    </DeleteRowButton>
                  </td>
                </tr>
              ))}
            </DataTable>

            <Pager
              page={page}
              totalPages={totalPages}
              total={total}
              pageSize={pageSize}
              busy={loading}
              onPage={setPage}
              onPageSize={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </main>
  );
}
