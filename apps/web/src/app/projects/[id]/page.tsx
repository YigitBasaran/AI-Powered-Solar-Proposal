"use client";

import { use, useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import { ActivityTimeline } from "@/components/activity/ActivityTimeline";
import { ProjectSettings } from "@/components/proposal/ProjectSettings";
import { RevisionTable } from "@/components/proposal/RevisionTable";
import { SendProposalPanel } from "@/components/proposal/SendProposalPanel";
import { ApiRequestError, api } from "@/lib/api";
import { Callout, Card, Row, SectionTitle, Spinner, cn } from "@/components/ui/primitives";
import type {
  ActivityEvent,
  ProjectResponse,
  Proposal,
  ProposalRevision,
} from "@/types/api";

/**
 * The internal view of one deal: who it is for, what was issued, whether it
 * was sent, and whether the customer has looked at it.
 *
 * The chat workspace at `/` is where a proposal is *built*. This is where it is
 * managed afterwards, which is a different job with a different shape - so it
 * is a separate route rather than another panel bolted onto the workspace.
 */
export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [revisions, setRevisions] = useState<ProposalRevision[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [loaded, { revisions: chain }, { events: history }] = await Promise.all([
        api.getProject(id),
        api.revisions(id),
        api.activity(id),
      ]);
      setProject(loaded);
      setRevisions(chain);
      setEvents(history);
      setError(null);

      // The current revision's own proposal, for the view stats. Read from the
      // public payload because that is the projection everything else uses -
      // and it is the one that proves the address is not in it.
      const mine = chain.find((row) => row.projectId === id && row.shareToken);
      setProposal(mine?.shareToken ? await api.proposal(mine.shareToken) : null);
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not reach the server. Check your connection and try again.",
      );
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <main className="mx-auto max-w-4xl p-4 sm:p-6">
        <h1 className="mb-3 text-lg font-semibold tracking-tight text-slate-ink">Project</h1>
        <Callout tone="warning" testId="project-error">
          {error}
        </Callout>
      </main>
    );
  }

  if (!project) {
    return (
      <main className="mx-auto max-w-4xl p-4 sm:p-6">
        <h1 className="mb-3 text-lg font-semibold tracking-tight text-slate-ink">Project</h1>
        <p className="flex items-center gap-2 text-[12.5px] text-slate-muted">
          <Spinner /> Loading…
        </p>
      </main>
    );
  }

  const current = revisions.find((row) => row.projectId === id);
  const views = proposal?.views;

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-4 p-4 sm:p-6">
      <div>
        {/* A back control, matching the customer page's: an arrow and a border,
            so it reads as "leave this page" rather than as body text. */}
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

        <h1 className="text-lg font-semibold tracking-tight text-slate-ink">
          {project.name ?? project.customer?.displayName ?? "Project"}
        </h1>
      </div>

      <Card className="p-4">
        <SectionTitle>Customer</SectionTitle>
        {project.customer ? (
          <dl>
            <Row label="Name" testId="project-customer-name">
              <a
                href={`/customers/${project.customer.customerId}`}
                className="text-navy-700 underline underline-offset-2"
              >
                {project.customer.displayName}
              </a>
            </Row>
            <Row label="Email">{project.customer.email}</Row>
          </dl>
        ) : (
          <p className="text-[12.5px] text-slate-muted" data-testid="no-customer">
            No customer linked. A proposal can still be finalised and shared by link, but it
            cannot be emailed until someone is linked to it.
          </p>
        )}
      </Card>

      <RevisionTable revisions={revisions} currentProjectId={id} />

      {current?.proposalId && current.shareToken ? (
        <>
          <SendProposalPanel
            proposalId={current.proposalId}
            shareToken={current.shareToken}
            publicUrl={`${typeof window === "undefined" ? "" : window.location.origin}/proposal/${current.shareToken}`}
          />

          <Card className="p-4">
            <SectionTitle>Customer engagement</SectionTitle>
            {views && views.viewCount > 0 ? (
              <dl>
                {/* "Page views", never "opens" - there is no email-open
                    tracking in this system, and the wording must not imply it. */}
                <Row label="Proposal-page views" testId="view-count">
                  {views.viewCount}
                </Row>
                <Row label="First viewed" testId="first-viewed">
                  {views.firstOpenedAt
                    ? new Date(views.firstOpenedAt).toLocaleString()
                    : "—"}
                </Row>
                <Row label="Last viewed" testId="last-viewed">
                  {views.lastOpenedAt ? new Date(views.lastOpenedAt).toLocaleString() : "—"}
                </Row>
              </dl>
            ) : (
              <p className="text-[12.5px] text-slate-muted" data-testid="not-yet-viewed">
                Not viewed yet.
              </p>
            )}
            <p className="mt-2 text-[11px] text-slate-muted">
              These are views of the proposal page. Email opens are not tracked.
            </p>
          </Card>
        </>
      ) : (
        <Card className="p-4">
          <SectionTitle>Proposal</SectionTitle>
          <p className="text-[12.5px] text-slate-muted">
            No proposal has been finalised for this revision yet.{" "}
            <a
              href={`/?project=${id}`}
              className="text-navy-700 underline underline-offset-2"
            >
              Continue in the workspace
            </a>
            .
          </p>
        </Card>
      )}

      <ProjectSettings
        project={project}
        customer={project.customer ?? null}
        onRenamed={setProject}
      />

      <ActivityTimeline events={events} />
    </main>
  );
}
