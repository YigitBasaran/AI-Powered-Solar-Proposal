"use client";

import { Card, DataTable, SectionTitle } from "@/components/ui/primitives";
import type { ProposalRevision } from "@/types/api";

/**
 * Every proposal issued for this deal, oldest first.
 *
 * A revision is a forked *project*, so this is a view over the project chain
 * rather than over a separate versions table. "Superseded" is derived - a
 * proposal is superseded when a later one exists - and deliberately not stored,
 * because writing a flag onto an issued document is exactly the mutation the
 * whole revision mechanism exists to avoid.
 *
 * Every row keeps a working link. A superseded proposal is still the document
 * that customer was sent, and it has to keep resolving.
 */
export function RevisionTable({
  revisions,
  currentProjectId,
}: {
  revisions: ProposalRevision[];
  currentProjectId?: string;
}) {
  return (
    <Card className="p-4">
      <SectionTitle>Revisions</SectionTitle>

      {revisions.length === 0 ? (
        <p className="text-[12.5px] text-slate-muted">
          No proposal has been finalised yet.
        </p>
      ) : (
        <DataTable
          label="Proposal revisions"
          headers={[
            { label: "Rev" },
            { label: "Reference" },
            { label: "Size", align: "right" },
            { label: "Production", align: "right" },
            { label: "Finalised" },
            { label: "Status" },
          ]}
        >
          {revisions.map((revision) => (
            <tr
              key={revision.projectId}
              data-testid={`revision-${revision.revisionNumber}`}
              data-superseded={revision.isSuperseded}
              data-current={revision.projectId === currentProjectId}
            >
              <td className="px-2 py-1.5 text-[12.5px]">
                {revision.revisionNumber}
              </td>
              <td className="px-2 py-1.5 text-[12.5px]">
                {revision.shareToken ? (
                  <a
                    href={`/proposal/${revision.shareToken}`}
                    className="text-navy-700 underline underline-offset-2"
                  >
                    {revision.reference ?? revision.shareToken.slice(0, 8)}
                  </a>
                ) : (
                  <span className="text-slate-muted">draft</span>
                )}
              </td>
              <td className="px-2 py-1.5 text-right text-[12.5px]">
                {revision.systemSizeKwp !== null
                  ? `${revision.systemSizeKwp} kWp`
                  : "—"}
              </td>
              <td className="px-2 py-1.5 text-right text-[12.5px]">
                {revision.annualProductionKwh !== null
                  ? `${Math.round(revision.annualProductionKwh).toLocaleString()} kWh`
                  : "—"}
              </td>
              <td className="px-2 py-1.5 text-[12.5px] text-slate-muted">
                {revision.finalisedAt
                  ? new Date(revision.finalisedAt).toLocaleDateString()
                  : "—"}
              </td>
              <td className="px-2 py-1.5 text-[12.5px]">
                {revision.proposalId === null
                  ? "Draft"
                  : revision.isSuperseded
                    ? "Superseded"
                    : "Current"}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
    </Card>
  );
}
