"use client";

import { Card, SectionTitle } from "@/components/ui/primitives";
import type { ActivityEvent } from "@/types/api";

/**
 * What happened to this deal, and when.
 *
 * The labels are a closed map rather than a prettified `eventType`, so a new
 * event type shows up as its raw name and gets noticed, instead of being
 * silently rendered as "Proposal Email Failed" and looking finished.
 *
 * Nothing here says "opened" about an email. The only opening this system can
 * observe is of the proposal *page*.
 */
const LABELS: Record<string, string> = {
  "customer.created": "Customer created",
  "customer.updated": "Customer details changed",
  "project.created": "Project started",
  "project.customer_assigned": "Customer linked",
  "project.revised": "Revision started",
  "analysis.completed": "Analysis completed",
  "analysis.failed": "Analysis failed",
  "proposal.finalised": "Proposal finalised",
  "proposal.send_requested": "Send requested",
  "proposal.email_sent": "Proposal emailed",
  "proposal.email_failed": "Email failed",
  "proposal.viewed": "Customer viewed the proposal",
  "proposal.pdf_downloaded": "PDF downloaded",
};

function describe(event: ActivityEvent): string | null {
  const meta = event.metadata ?? {};
  switch (event.eventType) {
    case "proposal.finalised":
      return `Revision ${meta.revisionNumber} · ${meta.systemSizeKwp} kWp · ${meta.reference}`;
    case "analysis.completed":
      return `${meta.systemSizeKwp} kWp · ${meta.annualProductionKwh} kWh/yr · ${meta.panelCount} panels`;
    case "proposal.email_sent":
      return `${meta.recipientMasked} · via ${meta.provider}`;
    case "proposal.email_failed":
      return `${meta.recipientMasked} · ${meta.errorCode}`;
    case "proposal.send_requested":
      return `${meta.recipientMasked}`;
    case "proposal.viewed":
      // "views", never "opens" - there is no email-open tracking anywhere.
      return `${meta.viewCount} page view${meta.viewCount === 1 ? "" : "s"} so far`;
    case "project.customer_assigned":
      return `${meta.displayName}${meta.forkedRevision ? " · forked a revision" : ""}`;
    case "project.created":
      // Named, because "Project started" on a customer with four projects
      // identifies none of them.
      return [meta.projectName, meta.customerName].filter(Boolean).join(" · ") || null;
    case "customer.updated":
      return `Changed: ${meta.changedFields}`;
    case "analysis.failed":
      return `${meta.errorCode}`;
    default:
      return null;
  }
}

export function ActivityTimeline({ events }: { events: ActivityEvent[] }) {
  return (
    <Card className="p-4">
      <SectionTitle>Activity</SectionTitle>

      {events.length === 0 ? (
        <p className="text-[12.5px] text-slate-muted">
          Nothing has happened yet.
        </p>
      ) : (
        <ol className="flex flex-col gap-2.5" data-testid="activity-timeline">
          {events.map((event) => {
            const detail = describe(event);
            return (
              <li
                key={event.eventId}
                className="flex gap-3 border-l-2 border-slate-line pl-3"
                data-testid={`activity-${event.eventType}`}
                data-actor={event.actor}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[12.5px] font-medium text-slate-ink">
                    {LABELS[event.eventType] ?? event.eventType}
                  </div>
                  {detail ? (
                    <div className="text-[11.5px] text-slate-muted">
                      {detail}
                    </div>
                  ) : null}
                </div>
                <time
                  className="shrink-0 text-[11px] text-slate-muted"
                  dateTime={event.occurredAt}
                >
                  {new Date(event.occurredAt).toLocaleString()}
                </time>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
