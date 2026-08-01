"use client";

import { useCallback, useEffect, useState } from "react";

import { DeleteRowButton } from "@/components/ui/DeleteRowButton";
import { Pager } from "@/components/ui/Pager";
import { Callout, DataTable, Spinner } from "@/components/ui/primitives";
import { ApiRequestError, api } from "@/lib/api";
import type { ProjectSummary } from "@/types/api";

/**
 * The projects table, paged and deletable, wherever projects are listed.
 *
 * Extracted because there were two of these: `/projects` had a table with a
 * pager and a delete button, and a customer's own screen had an unpaginated
 * `<ul>` with neither. Two renderings of one thing drift - and the one on the
 * customer screen had already lost the ability to delete a project at all.
 *
 * `customerId` narrows the same server route rather than reading the array on
 * `GET /customers/{id}`, so both callers share one query, one page size and one
 * definition of what a row shows.
 */
export function ProjectTable({
  customerId,
  query,
  emptyMessage,
  onDeleted,
}: {
  customerId?: string;
  query?: string;
  emptyMessage: string;
  /**
   * Fired after a row is deleted. The caller may hold counts of its own — the
   * customer screen warns how many projects a deletion would take with it —
   * and those have to be re-read rather than left describing a project that is
   * no longer there.
   */
  onDeleted?: () => void;
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (search: string, wanted: number, size: number) => {
      setLoading(true);
      try {
        const body = await api.listProjects({
          q: search || undefined,
          customerId,
          page: wanted,
          pageSize: size,
        });
        setProjects(body.projects);
        setTotal(body.total);
        setTotalPages(body.totalPages);
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
    },
    [customerId],
  );

  useEffect(() => {
    const timer = setTimeout(() => void load(query ?? "", page, pageSize), 200);
    return () => clearTimeout(timer);
  }, [query, page, pageSize, load]);

  useEffect(() => {
    setPage(1);
  }, [query, customerId]);

  return (
    <>
      {error ? (
        <Callout tone="warning" testId="projects-error">
          {error}{" "}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => void load(query ?? "", page, pageSize)}
          >
            Try again
          </button>
        </Callout>
      ) : null}

      {loading && projects.length === 0 ? (
        <p className="flex items-center gap-2 text-[12.5px] text-slate-muted">
          <Spinner /> Loading projects…
        </p>
      ) : null}

      {!loading && !error && projects.length === 0 ? (
        <p className="text-[12.5px] text-slate-muted" data-testid="projects-empty">
          {emptyMessage}
        </p>
      ) : null}

      {projects.length > 0 ? (
        <>
          <DataTable
            label="Projects"
            headers={[
              { label: "Project" },
              // The customer column is redundant on a customer's own screen,
              // where every row is theirs by construction.
              ...(customerId ? [] : [{ label: "Customer" }]),
              { label: "State" },
              { label: "Size", align: "right" as const },
              { label: "Created" },
              { label: "" },
            ]}
          >
            {projects.map((project) => (
              <tr key={project.projectId} data-testid="project-row">
                <td className="px-2 py-1.5">
                  <a
                    href={`/projects/${project.projectId}`}
                    className="text-[13px] font-medium text-navy-700 underline underline-offset-2"
                    data-testid={`project-row-${project.projectId}`}
                  >
                    {project.name ??
                      (project.hasProposal
                        ? (project.reference ?? "Proposal")
                        : "Draft project")}
                  </a>
                  {project.isRevision ? (
                    <span className="ml-2 text-[11px] text-slate-muted">revision</span>
                  ) : null}
                </td>
                {customerId ? null : (
                  <td className="px-2 py-1.5 text-[12.5px]">
                    {project.customer ? (
                      <a
                        href={`/customers/${project.customer.customerId}`}
                        className="text-navy-700 underline underline-offset-2"
                      >
                        {project.customer.displayName}
                      </a>
                    ) : (
                      // Said plainly rather than left blank: it is the reason
                      // this project cannot be emailed.
                      <span
                        className="text-slate-muted"
                        data-testid={`project-row-${project.projectId}-unlinked`}
                      >
                        No customer linked
                      </span>
                    )}
                  </td>
                )}
                <td className="px-2 py-1.5 text-[12.5px] text-slate-body">
                  {project.hasProposal
                    ? `Finalised · rev ${project.revisionNumber}`
                    : `In progress · ${project.analysisStatus}`}
                </td>
                <td className="px-2 py-1.5 text-right text-[12.5px]">
                  {project.systemSizeKwp !== null ? `${project.systemSizeKwp} kWp` : "—"}
                </td>
                <td className="px-2 py-1.5 text-[12px] text-slate-muted">
                  {new Date(project.createdAt).toLocaleDateString()}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <DeleteRowButton
                    label={`Delete ${project.name ?? "this project"}`}
                    title="Delete this project?"
                    testId={`delete-project-${project.projectId}`}
                    onDelete={() => api.deleteProject(project.projectId)}
                    onDeleted={() => {
                      void load(query ?? "", page, pageSize);
                      onDeleted?.();
                    }}
                  >
                    <p>
                      This removes the conversation and any draft analysis
                      {project.customer ? ` for ${project.customer.displayName}` : ""}.
                    </p>
                    <p className="mt-2">
                      A project that has already issued a proposal cannot be deleted — its share
                      link still resolves for the customer.
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
    </>
  );
}
