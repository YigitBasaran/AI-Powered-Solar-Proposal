"use client";

import type Konva from "konva";
import { Check, Copy, ExternalLink, FileDown, Sun } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel, ProgressRail } from "@/components/chat/ChatPanel";
import {
  CapacityWarning,
  EnergySection,
  FinancialSection,
  FxRow,
  KpiRow,
  RoofSection,
} from "@/components/proposal/AnalysisPanels";
import { RoofWorkspace } from "@/components/roof/RoofWorkspace";
import { Button, Card, SourceBadge, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { dataSourceLabel } from "@/lib/format";
import type {
  Analysis,
  ChatMessage,
  FinalizeResponse,
  HealthReady,
  MapConfig,
  ProgressStep,
  RoofModel,
} from "@/types/api";

export default function Home() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progress, setProgress] = useState<ProgressStep[]>([]);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [roof, setRoof] = useState<RoofModel | null>(null);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);
  const [health, setHealth] = useState<HealthReady | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [proposal, setProposal] = useState<FinalizeResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const stageRef = useRef<Konva.Stage | null>(null);

  // Boot: create a project and load the static roof/map configuration.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [created, roofModel, config, ready] = await Promise.all([
          api.createProject(),
          api.roofModel(),
          api.mapConfig(),
          api.ready(),
        ]);
        if (cancelled) return;
        setProjectId(created.projectId);
        setProgress(created.progress);
        setMessages([
          {
            role: "assistant",
            content: created.assistantMessage,
            step: created.currentStep,
            parserSource: null,
            createdAt: new Date().toISOString(),
          },
        ]);
        setRoof(roofModel);
        setMapConfig(config);
        setHealth(ready);
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? `${caught.message} — is the API running on port 8000?`
              : "Could not reach the API.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const runAnalysis = useCallback(
    async (id: string) => {
      setBusy("Reconstructing roof and placing panels…");
      try {
        const result = await api.runAnalysis(id);
        setAnalysis(result.analysis);
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: buildResultSummary(result.analysis),
            step: result.currentStep,
            parserSource: "deterministic",
            createdAt: new Date().toISOString(),
          },
        ]);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Analysis failed.");
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const send = useCallback(
    async (message: string) => {
      if (!projectId) return;
      setError(null);
      setPending(true);
      setMessages((current) => [
        ...current,
        {
          role: "user",
          content: message,
          step: null,
          parserSource: null,
          createdAt: new Date().toISOString(),
        },
      ]);

      try {
        const response = await api.chat(projectId, message);
        setProgress(response.progress);
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: response.assistantMessage,
            step: response.currentStep,
            parserSource: response.parserSource,
            createdAt: new Date().toISOString(),
          },
        ]);
        if (response.readyForAnalysis) await runAnalysis(projectId);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Message failed.");
      } finally {
        setPending(false);
      }
    },
    [projectId, runAnalysis],
  );

  const finalize = useCallback(async () => {
    if (!projectId) return;
    setBusy("Creating your proposal…");
    try {
      const result = await api.finalize(projectId);
      setProposal(result);

      // Export the completed stage so the PDF shows the real satellite layout.
      // The image is same-origin, so the canvas is not tainted.
      const stage = stageRef.current;
      if (stage) {
        const dataUrl = stage.toDataURL({ pixelRatio: 2, mimeType: "image/png" });
        const blob = await (await fetch(dataUrl)).blob();
        await api.uploadLayoutSnapshot(projectId, blob).catch(() => {
          // A missing snapshot must not block the proposal: the PDF falls back
          // to drawing the roof from stored geometry.
        });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the proposal.");
    } finally {
      setBusy(null);
    }
  }, [projectId]);

  const copyLink = useCallback(async () => {
    if (!proposal) return;
    await navigator.clipboard.writeText(proposal.shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [proposal]);

  const llm = health?.checks?.llm;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-slate-line bg-navy-900 text-white">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Sun className="size-5 text-solar-500" aria-hidden />
            <span className="text-[15px] font-semibold tracking-tight">solarVis AI</span>
            <span className="ml-2 hidden text-[12px] text-white/55 sm:inline">
              AI-powered solar proposal flow
            </span>
          </div>
          <div className="flex items-center gap-2 text-[11.5px]">
            {llm ? (
              <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5">
                Parser: {llm.provider === "ollama" ? `Ollama · ${llm.model}` : "deterministic rules"}
              </span>
            ) : null}
            {mapConfig ? (
              <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5">
                Imagery: {mapConfig.isLive ? "live" : "demo fixture"}
              </span>
            ) : null}
            {analysis ? (
              <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5">
                FX: {dataSourceLabel(analysis.exchangeRate.retrievalSource).label}
              </span>
            ) : null}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-4">
        {error ? (
          <div className="mb-3 rounded-lg border-l-4 border-[#d03b3b] bg-[#fdeeee] px-3.5 py-2.5 text-[12.5px] text-[#8a1f1f]">
            {error}
          </div>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[minmax(340px,35%)_1fr]">
          <Card className="flex max-h-[calc(100vh-8rem)] min-h-[520px] flex-col overflow-hidden lg:sticky lg:top-[4.25rem]">
            <div className="border-b border-slate-line px-3.5 py-2.5">
              <ProgressRail steps={progress} />
            </div>
            {projectId ? (
              <ChatPanel messages={messages} onSend={send} pending={pending} />
            ) : (
              <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-slate-muted">
                <Spinner /> Starting session…
              </div>
            )}
          </Card>

          <div className="space-y-4">
            <RoofWorkspace
              roof={roof}
              analysis={analysis}
              mapConfig={mapConfig}
              busy={busy}
              onStageReady={(stage) => {
                stageRef.current = stage;
              }}
            />

            {analysis ? (
              <>
                <CapacityWarning warning={analysis.layout.capacityWarning} />
                <KpiRow analysis={analysis} />
                <FxRow analysis={analysis} />

                <Card className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-3">
                  <div className="text-[12.5px] text-slate-body">
                    {proposal ? (
                      <span className="flex items-center gap-2">
                        <SourceBadge tone="live" label="Proposal created" />
                        <code className="rounded bg-surface-2 px-1.5 py-0.5 text-[11.5px]">
                          {proposal.shareUrl}
                        </code>
                      </span>
                    ) : (
                      "Create a shareable proposal with a permanent link and a PDF."
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {proposal ? (
                      <>
                        <Button variant="secondary" onClick={copyLink}>
                          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                          {copied ? "Copied" : "Copy link"}
                        </Button>
                        <a href={api.pdfUrl(proposal.shareToken)} target="_blank" rel="noreferrer">
                          <Button variant="secondary">
                            <FileDown className="size-3.5" /> Download PDF
                          </Button>
                        </a>
                        <a href={`/proposal/${proposal.shareToken}`} target="_blank" rel="noreferrer">
                          <Button>
                            <ExternalLink className="size-3.5" /> Open proposal
                          </Button>
                        </a>
                      </>
                    ) : (
                      <Button onClick={finalize} disabled={Boolean(busy)}>
                        Create proposal
                      </Button>
                    )}
                  </div>
                </Card>

                <div className="grid gap-4 xl:grid-cols-2">
                  <EnergySection analysis={analysis} />
                  <FinancialSection analysis={analysis} />
                </div>
                <RoofSection analysis={analysis} />
              </>
            ) : null}
          </div>
        </div>
      </main>

      <footer className="border-t border-slate-line px-4 py-3 text-[11px] text-slate-muted">
        Feasibility estimate from satellite imagery and modelled irradiation — not a site
        survey or a binding quotation.
      </footer>
    </div>
  );
}

function buildResultSummary(analysis: Analysis): string {
  const { layout, energy, financial, exchangeRate } = analysis;
  const lines = [
    `Analysis complete.`,
    ``,
    `Placed ${layout.placedPanelCount} panels (${layout.feasibleSystemSizeKwp} kWp) across ` +
      `${layout.facets.length} roof facet${layout.facets.length === 1 ? "" : "s"}.`,
    `Annual production: ${Math.round(energy.totalAnnualProductionKwh).toLocaleString("en-GB")} kWh ` +
      `— ${financial.coveragePercent.toFixed(1)}% of your ` +
      `${Math.round(financial.annualConsumptionKwh).toLocaleString("en-GB")} kWh usage.`,
    `Annual saving: €${financial.annualSavingsEur}.`,
    `Capital cost $${financial.originalCapex.amount} converted at ` +
      `${exchangeRate.rate} (${exchangeRate.dataProvider}, ${exchangeRate.rateDate}) ` +
      `= €${financial.convertedCapex.amount}.`,
    financial.simplePaybackYears
      ? `Simple payback: ${financial.simplePaybackYears.toFixed(2)} years.`
      : `The system does not pay back within the analysis period.`,
  ];
  if (layout.capacityWarning) lines.push(``, layout.capacityWarning);
  return lines.join("\n");
}
