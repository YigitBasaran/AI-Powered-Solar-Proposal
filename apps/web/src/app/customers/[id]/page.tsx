"use client";

import { use, useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import { ActivityTimeline } from "@/components/activity/ActivityTimeline";
import { CustomerEditor } from "@/components/customers/CustomerEditor";
import { ProjectTable } from "@/components/projects/ProjectTable";
import { ApiRequestError, api } from "@/lib/api";
import {
  Button,
  Callout,
  Card,
  Row,
  SectionTitle,
  Spinner,
  cn,
} from "@/components/ui/primitives";
import type { ActivityEvent, Customer, CustomerProject } from "@/types/api";

/**
 * One customer, in three tabs.
 *
 * Details, Projects and Activity are sections of the *same* record, so they
 * are tabs rather than routes: switching between them should not cost a
 * navigation, and the back control should still lead to the customer list
 * rather than to whichever section was open a moment ago.
 *
 * The project list is what makes this screen worth having. Without it a
 * project became unreachable the moment you left the workspace that created
 * it — there is no project id anyone keeps.
 */
const TABS = [
  { id: "details", label: "Details" },
  { id: "projects", label: "Projects" },
  { id: "activity", label: "Activity" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function CustomerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const [tab, setTab] = useState<TabId>("details");
  const [customer, setCustomer] = useState<Customer | null>(null);
  // The *complete* list, not the table's current page. It backs the counts in
  // the delete-customer warning, which must say how many projects would go with
  // them - a page-sized number there would understate a destructive action.
  const [projects, setProjects] = useState<CustomerProject[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const loadCustomer = useCallback(() => {
    return api
      .getCustomer(id)
      .then((detail) => {
        setCustomer(detail.customer);
        setProjects(detail.projects);
        setEvents(detail.activity);
      })
      .catch((caught) => {
        setError(
          caught instanceof ApiRequestError
            ? caught.message
            : "Could not reach the server. Check your connection and try again.",
        );
      });
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    api
      .getCustomer(id)
      .then((detail) => {
        if (cancelled) return;
        setCustomer(detail.customer);
        setProjects(detail.projects);
        setEvents(detail.activity);
      })
      .catch((caught) => {
        if (cancelled) return;
        setError(
          caught instanceof ApiRequestError
            ? caught.message
            : "Could not reach the server. Check your connection and try again.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function startProject() {
    setStarting(true);
    try {
      const created = await api.createProject({ customerId: id, name: undefined });
      window.location.href = `/?project=${created.projectId}`;
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not start a project.",
      );
      setStarting(false);
    }
  }

  if (error && !customer) {
    return (
      <main className="mx-auto max-w-3xl p-4 sm:p-6">
        <h1 className="mb-3 text-lg font-semibold tracking-tight text-slate-ink">
          Customer
        </h1>
        <Callout tone="warning" testId="customer-error">
          {error}
        </Callout>
      </main>
    );
  }

  if (!customer) {
    return (
      <main className="mx-auto max-w-3xl p-4 sm:p-6">
        <h1 className="mb-3 text-lg font-semibold tracking-tight text-slate-ink">
          Customer
        </h1>
        <p className="flex items-center gap-2 text-[12.5px] text-slate-muted">
          <Spinner /> Loading…
        </p>
      </main>
    );
  }

  const issuedCount = projects.filter((project) => project.hasProposal).length;

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 p-4 sm:p-6">
      <div>
        {/* A back control, not a bare link: it carries an arrow and a border so
            it reads as "leave this page" rather than as body text. */}
        <a
          href="/customers"
          data-testid="back-to-customers"
          className={cn(
            "mb-2 inline-flex items-center gap-1.5 rounded-lg border border-slate-line",
            "bg-surface px-2.5 py-1 text-[12px] text-slate-body hover:bg-surface-2",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
          )}
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          Return to all customers
        </a>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1
            className="text-lg font-semibold tracking-tight text-slate-ink"
            data-testid="customer-name"
          >
            {customer.displayName}
          </h1>
          <Button
            onClick={() => void startProject()}
            disabled={starting}
            testId="start-project"
          >
            {starting ? "Starting…" : "New project"}
          </Button>
        </div>
      </div>

      {customer.archivedAt ? (
        <Callout tone="info" testId="customer-archived">
          This customer is archived. Their proposals are unaffected and their
          links still work.
        </Callout>
      ) : null}

      {/* A tablist, not three links: the selected one is announced as such and
          the arrow keys move between them, which is what a screen reader and a
          keyboard user expect from something that swaps content in place. */}
      <div
        role="tablist"
        aria-label="Customer sections"
        className="flex gap-1 border-b border-slate-line"
      >
        {TABS.map(({ id: tabId, label }) => (
          <button
            key={tabId}
            type="button"
            role="tab"
            id={`tab-${tabId}`}
            aria-selected={tab === tabId}
            aria-controls={`panel-${tabId}`}
            data-testid={`tab-${tabId}`}
            onClick={() => setTab(tabId)}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-[13px]",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
              tab === tabId
                ? "border-navy-700 font-medium text-navy-900"
                : "border-transparent text-slate-muted hover:text-slate-body",
            )}
          >
            {label}
            {tabId === "projects" && projects.length > 0 ? (
              <span className="ml-1.5 text-[11px] text-slate-muted">
                {projects.length}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {error ? (
        <Callout tone="warning" testId="customer-error">
          {error}
        </Callout>
      ) : null}

      {tab === "details" ? (
        <div role="tabpanel" id="panel-details" aria-labelledby="tab-details">
          <Card className="p-4">
            <SectionTitle>Contact</SectionTitle>
            <dl className="mb-3">
              <Row label="Email" testId="customer-email">
                {customer.email}
              </Row>
              {customer.phone ? (
                <Row label="Phone">{customer.phone}</Row>
              ) : null}
              {customer.companyName ? (
                <Row label="Company">{customer.companyName}</Row>
              ) : null}
              {customer.address ? (
                <Row label="Address">{customer.address}</Row>
              ) : null}
              <Row label="Added">
                {new Date(customer.createdAt).toLocaleDateString()}
              </Row>
            </dl>

            <CustomerEditor
              customer={customer}
              projectCount={projects.length}
              issuedCount={issuedCount}
              onChanged={setCustomer}
              onDeleted={() => {
                window.location.href = "/customers";
              }}
            />
          </Card>
        </div>
      ) : null}

      {tab === "projects" ? (
        <div role="tabpanel" id="panel-projects" aria-labelledby="tab-projects">
          <Card className="p-4">
            <SectionTitle>Projects</SectionTitle>

            {/*
              The same table `/projects` uses, narrowed to this customer, so a
              project can be paged and deleted from here too. It used to be a
              plain list with neither.
            */}
            <div data-testid="customer-projects">
              <ProjectTable
                customerId={id}
                emptyMessage="No projects yet. Start one to build a proposal for this customer."
                onDeleted={() => void loadCustomer()}
              />
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "activity" ? (
        <div role="tabpanel" id="panel-activity" aria-labelledby="tab-activity">
          <ActivityTimeline events={events} />
        </div>
      ) : null}
    </main>
  );
}
