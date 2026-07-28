"use client";

import { Check, Copy, Eye, FileDown, Sun } from "lucide-react";
import { use, useCallback, useEffect, useState } from "react";

import {
  CapacityWarning,
  EnergySection,
  FinancialSection,
  FxRow,
  KpiRow,
  RoofSection,
} from "@/components/proposal/AnalysisPanels";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Proposal } from "@/types/api";

/**
 * The public, read-only proposal.
 *
 * Everything here comes from the stored snapshot. Nothing is recalculated, so
 * the figures match the PDF exactly and do not move when the market does.
 */
export default function ProposalPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.proposal(token);
        if (cancelled) return;
        setProposal(data);
        // Fire-and-forget: a tracking failure must never break the page.
        api.recordView(token).catch(() => undefined);
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error ? caught.message : "This proposal link is not valid.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const copyLink = useCallback(async () => {
    await navigator.clipboard.writeText(window.location.href);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, []);

  if (error) {
    return (
      <main className="mx-auto flex min-h-screen max-w-lg items-center px-4">
        <Card className="w-full px-5 py-6 text-center" >
          <h1 data-testid="proposal-not-found" className="text-base font-semibold text-slate-ink">
            Proposal not found
          </h1>
          <p className="mt-1.5 text-[13px] text-slate-body">{error}</p>
          <p className="mt-3 text-[12px] text-slate-muted">
            Check the link, or ask for a new one to be sent to you.
          </p>
        </Card>
      </main>
    );
  }

  if (!proposal) {
    return (
      <main
        role="status"
        className="flex min-h-screen items-center justify-center gap-2 text-[13px] text-slate-muted"
      >
        <Spinner /> Loading proposal…
      </main>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-slate-line bg-navy-900 text-white">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <Sun className="size-5 text-solar-500" aria-hidden />
            <div>
              <h1 data-testid="proposal-title" className="text-[15px] font-semibold tracking-tight">
                Solar Feasibility Proposal
              </h1>
              <div className="text-[11.5px] text-white/55">
                Prepared by solarVis AI · {shortDate(proposal.createdAt)}
              </div>
            </div>
          </div>
          <div className="no-print flex flex-wrap items-center gap-2">
            {proposal.views ? (
              <span
                data-testid="view-count"
                data-count={proposal.views.viewCount}
                className="flex items-center gap-1 rounded-full border border-white/15 bg-white/5 px-2 py-0.5 text-[11.5px]"
              >
                <Eye className="size-3" aria-hidden />
                {proposal.views.viewCount} view
                {proposal.views.viewCount === 1 ? "" : "s"}
              </span>
            ) : null}
            <Button variant="secondary" onClick={copyLink}>
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "Copied" : "Copy link"}
            </Button>
            <a href={api.pdfUrl(token)} target="_blank" rel="noreferrer" data-testid="pdf-link">
              <Button variant="secondary">
                <FileDown className="size-3.5" /> Download PDF
              </Button>
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1200px] flex-1 space-y-4 px-4 py-5">
        <CapacityWarning warning={proposal.capacityWarning ?? proposal.layout.capacityWarning} />

        {proposal.aiSummary ? (
          <Card className="px-3.5 py-3 text-[13px] leading-relaxed text-slate-body">
            {proposal.aiSummary}
          </Card>
        ) : null}

        <KpiRow analysis={proposal} />
        <FxRow analysis={proposal} />

        {proposal.layoutSnapshotUrl ? (
          <Card className="overflow-hidden">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              data-testid="layout-snapshot"
              src={proposal.layoutSnapshotUrl}
              alt="Satellite view with the roof reconstruction and panel layout"
              className="w-full"
            />
          </Card>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-2">
          <EnergySection analysis={proposal} />
          <FinancialSection analysis={proposal} />
        </div>

        <RoofSection analysis={proposal} />

        <Card className="px-3.5 py-3 text-[11.5px] leading-relaxed text-slate-muted">
          <strong className="text-slate-body">Assumptions.</strong> Fixed case property with a
          uniform {proposal.roof.pitchDeg}° pitch. Panels 1 m × 2 m at 400 Wp, flush-mounted.
          Savings capped at consumption (no export compensation). Flat tariff, no degradation,
          inflation, maintenance or financing cost. No shading or obstruction analysis. The
          exchange rate shown was fixed when this proposal was created and is stored with it —
          reopening this page later will not recalculate the figures at a newer rate.
        </Card>
      </main>

      <footer className="border-t border-slate-line px-4 py-3 text-center text-[11px] text-slate-muted">
        Feasibility estimate from satellite imagery and modelled irradiation — not a site survey
        or a binding quotation.
      </footer>
    </div>
  );
}
