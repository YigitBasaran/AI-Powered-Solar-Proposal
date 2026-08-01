"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button, cn } from "@/components/ui/primitives";

export const PAGE_SIZES = [10, 25, 50, 100] as const;

/**
 * Page controls for a server-paginated list.
 *
 * It reports the *total*, not just "there might be more". A pager that only
 * knows whether a next page exists cannot say "page 3 of 9", and one that
 * discovers the total by fetching every page has defeated the point of paging
 * — so the count comes from the API alongside the rows.
 *
 * Changing the page size returns to page 1. Staying on page 7 while the pages
 * get four times larger lands somewhere unrelated to what was being read.
 */
export function Pager({
  page,
  totalPages,
  total,
  pageSize,
  onPage,
  onPageSize,
  busy,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
  busy?: boolean;
}) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-slate-line pt-3">
      <div className="flex items-center gap-2 text-[12px] text-slate-muted">
        <label htmlFor="page-size" className="whitespace-nowrap">
          Rows per page
        </label>
        <select
          id="page-size"
          value={pageSize}
          disabled={busy}
          data-testid="page-size"
          onChange={(event) => onPageSize(Number(event.target.value))}
          className={cn(
            "rounded-lg border border-slate-line bg-surface px-2 py-1 text-[12px] text-slate-ink",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-navy-700",
          )}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-[12px] text-slate-muted" data-testid="page-summary">
          {total === 0 ? "No rows" : `${first}–${last} of ${total}`}
        </span>

        <div className="flex items-center gap-1">
          <Button
            variant="secondary"
            disabled={busy || page <= 1}
            onClick={() => onPage(page - 1)}
            testId="page-previous"
            title="Previous page"
          >
            <ChevronLeft className="size-3.5" aria-hidden />
            <span className="sr-only sm:not-sr-only">Previous</span>
          </Button>

          <span className="px-1 text-[12px] text-slate-body" data-testid="page-indicator">
            Page {page} of {totalPages}
          </span>

          <Button
            variant="secondary"
            disabled={busy || page >= totalPages}
            onClick={() => onPage(page + 1)}
            testId="page-next"
            title="Next page"
          >
            <span className="sr-only sm:not-sr-only">Next</span>
            <ChevronRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
