"use client";

import type Konva from "konva";
import { Check, Copy, ExternalLink, FileDown } from "lucide-react";
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
import { FxChip, useStatusChip } from "@/components/layout/StatusBand";
import { RoofWorkspace } from "@/components/roof/RoofWorkspace";
import {
  Button,
  Callout,
  Card,
  SectionTitle,
  SourceBadge,
  Spinner,
  cn,
} from "@/components/ui/primitives";
import { ApiRequestError, api } from "@/lib/api";
import type {
  Analysis,
  ChatMessage,
  FinalizeResponse,
  MapConfig,
  ProgressStep,
  RoofModel,
} from "@/types/api";

/**
 * The three sizes the case permits. Selecting a card sends the same canonical
 * phrase the chat parser accepts, so a card click and a typed message take
 * exactly one backend path - there is no second code path to drift.
 */
const SYSTEM_SIZES = [
  { kwp: 3.6, panels: 9, phrase: "3.6 kWp" },
  { kwp: 6, panels: 15, phrase: "6 kWp" },
  { kwp: 9.6, panels: 24, phrase: "9.6 kWp" },
] as const;

export default function Home() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [needsProject, setNeedsProject] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progress, setProgress] = useState<ProgressStep[]>([]);
  const [currentStep, setCurrentStep] = useState<string>("location");
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [roof, setRoof] = useState<RoofModel | null>(null);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);
  // Readiness is read by the shared status band now; the workspace keeps only
  // what it renders itself.
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<string>("pending");
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<FinalizeResponse | null>(null);
  /** The document issued before the current edit forked a revision. */
  const [supersededProposal, setSupersededProposal] = useState<FinalizeResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const stageRef = useRef<Konva.Stage | null>(null);
  const captureRef = useRef<(() => Promise<string | null>) | null>(null);

  // Boot: open the project the URL names, or start a new one.
  //
  // `?project=<id>` is how every link from the customer and project screens
  // arrives here. Without honouring it this effect created a *fresh* project on
  // every visit, so clicking "continue in workspace" on a half-finished job
  // silently discarded it and presented an empty chat — the work was still in
  // the database, and nothing in the UI could reach it again.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const requested = new URLSearchParams(window.location.search).get("project");

        const [roofModel, config] = await Promise.all([api.roofModel(), api.mapConfig()]);
        if (cancelled) return;
        setRoof(roofModel);
        setMapConfig(config);

        if (requested) {
          const existing = await api.getProject(requested);
          if (cancelled) return;
          setProjectId(existing.projectId);
          setProgress(existing.progress);
          setCurrentStep(existing.currentStep);
          // The stored transcript, so the conversation resumes where it was
          // rather than restarting with a greeting.
          setMessages(existing.messages);
          setAnalysis(existing.analysis);
          setAnalysisStatus(existing.analysisStatus);
          setAnalysisError(existing.analysisError?.message ?? null);

          // Restore the issued proposal too, if there is one. Without this a
          // reopened project offered "Create proposal" for a document it had
          // already issued — the link and the PDF were simply missing from a
          // project that had both.
          if (existing.hasProposal) {
            const { revisions } = await api.revisions(requested);
            const mine = revisions.find((row) => row.projectId === requested);
            if (!cancelled && mine?.shareToken && mine.proposalId) {
              setProposal({
                proposalId: mine.proposalId,
                shareToken: mine.shareToken,
                shareUrl: `${window.location.origin}/proposal/${mine.shareToken}`,
                pdfUrl: api.pdfUrl(mine.shareToken),
                capacityWarning: null,
              });
            }
          }
          return;
        }

        // Nothing is created here any more.
        //
        // Opening the workspace used to POST /projects on load, so every visit
        // - including a stray refresh - left behind a project belonging to
        // nobody, with no name, that showed up on the all-projects list and
        // could never be emailed. A project is started deliberately, for a
        // customer, from /projects/new.
        if (!cancelled) setNeedsProject(true);
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
        setAnalysisStatus("complete");
        setCurrentStep(result.currentStep);
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
        setAnalysisError(null);
      } catch (caught) {
        // The server has already recorded why and marked the project failed.
        // Mirroring it here is what turns a transient toast into a state the
        // page can explain and offer a retry from.
        const message = caught instanceof Error ? caught.message : "Analysis failed.";
        setError(message);
        setAnalysisStatus("failed");
        setAnalysisError(message);
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  /** Re-read the stored analysis after the server changed or cleared it. */
  const refreshAnalysis = useCallback(async (id: string) => {
    try {
      const project = await api.getProject(id);
      setAnalysis(project.analysis);
      setAnalysisStatus(project.analysisStatus);
      setAnalysisError(project.analysisError?.message ?? null);
      setCurrentStep(project.currentStep);
      setProgress(project.progress);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not reload the analysis.");
    }
  }, []);

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
        setCurrentStep(response.currentStep);
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: response.assistantMessage,
            step: response.currentStep,
            parserSource: response.parserSource,
            interpretation: response.interpretation ?? null,
            createdAt: new Date().toISOString(),
          },
        ]);
        // A change to a finalised project forks a revision and the conversation
        // moves with it. Without this the next message would land on the parent,
        // which is immutable — the edit would appear to be silently ignored.
        if (response.projectId !== projectId) {
          setProjectId(response.projectId);

          // And the issued proposal stops being *this* project's proposal.
          //
          // It was left in place, so "Open proposal" and "Download PDF" kept
          // pointing at the document issued before the change — showing the old
          // figures beside the new ones on screen and reading, correctly but
          // uselessly, as though the edit had not taken. The old link is not
          // broken and must not be: it is what the customer was sent. It is
          // moved out of the way and named, and the revision has to be
          // finalised to get one of its own.
          setSupersededProposal(proposal);
          setProposal(null);
        }

        setAnalysisStatus(response.analysisStatus ?? analysisStatus);

        if (response.readyForAnalysis) {
          await runAnalysis(response.projectId);
        } else if (
          response.recalculated?.length ||
          (analysis !== null && response.analysisStatus !== "complete")
        ) {
          // Three cases, one rule: whenever the server says the stored analysis
          // no longer describes the project's current inputs, re-read it rather
          // than leave figures on screen that look current. A correction
          // recomputes the dependent sections; a reset clears the analysis
          // outright; a *failed* recompute leaves it stale, and that is the one
          // where doing nothing would be worst — the old numbers are still
          // there, still plausible, and now describe something else.
          await refreshAnalysis(response.projectId);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Message failed.");
      } finally {
        setPending(false);
      }
    },
    // `proposal` is listed because the fork branch reads it to move the issued
    // document aside. Omitted, the callback closed over the value from when it
    // was created - null on first render - so the supersession never fired and
    // the stale link stayed on screen.
    [projectId, runAnalysis, refreshAnalysis, analysis, analysisStatus, proposal],
  );

  const finalize = useCallback(async () => {
    if (!projectId) return;
    setBusy("Creating your proposal…");
    try {
      // Retried once on a transport failure, because finalisation is
      // idempotent server-side: it returns the proposal it already issued
      // without touching the model. The failure this recovers from is a lost
      // *response* rather than lost work — a proxy that gave up while the
      // request was still open leaves a proposal that exists and a customer
      // looking at an error.
      let result: FinalizeResponse;
      try {
        result = await api.finalize(projectId);
      } catch (first) {
        if (first instanceof ApiRequestError) throw first;
        result = await api.finalize(projectId);
      }
      setProposal(result);

      // Export a *roof-framed* stage so the PDF shows the panels clearly.
      //
      // This used to export the operator's live view, so a proposal issued
      // while zoomed out to check the neighbourhood shipped a picture in which
      // the roof was a smudge - and one issued while panned elsewhere shipped
      // a picture of somewhere else entirely. The capture re-frames on the
      // roof, takes the shot, and puts the operator's view back.
      const capture = captureRef.current;
      const dataUrl = capture ? await capture() : null;
      if (dataUrl) {
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

  // The FX chip describes *this* analysis's rate rather than the deployment's
  // configuration, so it is published into the shared band by the page that
  // has the figures.
  useStatusChip(
    analysis ? <FxChip retrievalSource={analysis.exchangeRate.retrievalSource} /> : null,
  );

  // Reached by opening the workspace with no project in the URL. Rather than
  // manufacturing one, say what is missing and point at the place that starts
  // a real project — for a real customer, with a name.
  if (needsProject) {
    return (
      <main className="mx-auto flex max-w-2xl flex-col gap-4 p-4 sm:p-6">
        <h1 className="text-lg font-semibold tracking-tight text-slate-ink">Workspace</h1>
        <Card className="p-4">
          <SectionTitle>No project open</SectionTitle>
          <p className="mb-3 text-[12.5px] text-slate-body" data-testid="no-project-open">
            The workspace builds a proposal for a specific project. Start one for a customer,
            or reopen an existing project — every project is listed under its customer and on
            the projects page.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                window.location.href = "/projects/new";
              }}
              testId="start-a-project"
            >
              Add New Project
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                window.location.href = "/projects";
              }}
            >
              Open an existing project
            </Button>
          </div>
        </Card>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-4">
        {error ? (
          <div
            role="alert"
            data-testid="error-banner"
            className="mb-3 rounded-lg border-l-4 border-[#d03b3b] bg-[#fdeeee] px-3.5 py-2.5 text-[12.5px] text-[#8a1f1f]"
          >
            {error}
          </div>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[minmax(340px,35%)_1fr]">
          <Card className="flex max-h-[calc(100vh-8rem)] min-h-[520px] flex-col overflow-hidden lg:sticky lg:top-[4.25rem]">
            <div className="border-b border-slate-line px-3.5 py-2.5">
              <ProgressRail steps={progress} />
            </div>
            {projectId ? (
              <>
                {currentStep === "system_size" ? (
                  <div
                    data-testid="system-size-cards"
                    className="border-b border-slate-line px-3.5 py-2.5"
                  >
                    <div className="mb-1.5 text-[11.5px] font-medium text-slate-body">
                      Choose a system size
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {SYSTEM_SIZES.map((size) => (
                        <button
                          key={size.kwp}
                          type="button"
                          disabled={pending || Boolean(busy)}
                          data-testid={`system-size-${size.kwp}`}
                          onClick={() => send(size.phrase)}
                          className={cn(
                            "rounded-lg border border-slate-line px-2 py-2 text-left transition-colors",
                            "hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2",
                            "focus-visible:outline-navy-700 disabled:cursor-not-allowed disabled:opacity-50",
                          )}
                        >
                          <div className="text-[13px] font-semibold text-slate-ink">
                            {size.kwp} kWp
                          </div>
                          <div className="text-[11px] text-slate-muted">
                            {size.panels} panels
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
                <ChatPanel messages={messages} onSend={send} pending={pending} />
              </>
            ) : (
              <div
                role="status"
                className="flex flex-1 items-center justify-center gap-2 text-[13px] text-slate-muted"
              >
                <Spinner /> Starting session…
              </div>
            )}
          </Card>

          {/* min-w-0: this column holds the Konva stage, which renders at its
              default width until the ResizeObserver measures it. Without this
              the grid track widens to fit and never shrinks back. */}
          <div className="min-w-0 space-y-4">
            <RoofWorkspace
              roof={roof}
              analysis={analysis}
              mapConfig={mapConfig}
              busy={busy}
              onStageReady={(stage) => {
                stageRef.current = stage;
              }}
              onCaptureReady={(capture) => {
                captureRef.current = capture;
              }}
            />

            {analysisStatus === "failed" ? (
              <Callout
                testId="failed-analysis"
                title="The production model could not be retrieved."
              >
                {analysisError ??
                  "Live solar production data was not available, so no figures were produced."}{" "}
                Nothing is shown rather than an estimate, because a proposal has to state
                a measured yield. Try again — the proposal cannot be created until it
                succeeds.
                <span className="mt-2.5 block">
                  <Button
                    variant="secondary"
                    onClick={() => projectId && runAnalysis(projectId)}
                    disabled={Boolean(busy)}
                    testId="retry-analysis"
                  >
                    Retry the analysis
                  </Button>
                </span>
              </Callout>
            ) : null}

            {analysis ? (
              <>
                {analysisStatus === "stale" ? (
                  <Callout testId="stale-analysis" title="These figures are out of date.">
                    The recalculation after your last change did not complete, so what
                    follows still describes the previous inputs. Re-run the analysis
                    before relying on it — the proposal cannot be created until then.
                  </Callout>
                ) : null}
                <CapacityWarning warning={analysis.layout.capacityWarning} />
                <KpiRow analysis={analysis} />
                <FxRow analysis={analysis} />

                <Card className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-3">
                  <div className="text-[12.5px] text-slate-body">
                    {proposal ? (
                      <span className="flex flex-wrap items-center gap-2">
                        <SourceBadge tone="live" label="Proposal created" />
                        <code
                          data-testid="share-url"
                          data-share-token={proposal.shareToken}
                          // A share URL is one long unbreakable token; without
                          // break-all it pushes a phone-width page sideways.
                          className="max-w-full break-all rounded bg-surface-2 px-1.5 py-0.5 text-[11.5px]"
                        >
                          {proposal.shareUrl}
                        </code>
                      </span>
                    ) : supersededProposal ? (
                      // The figures on screen are a *draft* revision. Saying so
                      // is the whole point: the previously issued document is
                      // still live and still shows the old numbers, and until
                      // this revision is finalised there is nothing newer to
                      // send anyone.
                      <span className="flex flex-col gap-1" data-testid="revision-pending">
                        <span>
                          <strong>These figures are not issued yet.</strong> Your change
                          started a revision — create a proposal to give it its own link.
                        </span>
                        <span className="text-[11.5px] text-slate-muted">
                          The proposal you already sent still shows the previous figures, and
                          its link keeps working:{" "}
                          <a
                            href={`/proposal/${supersededProposal.shareToken}`}
                            target="_blank"
                            rel="noreferrer"
                            className="text-navy-700 underline underline-offset-2"
                            data-testid="superseded-link"
                          >
                            open the issued proposal
                          </a>
                        </span>
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
                      <Button
                        onClick={finalize}
                        // Refused server-side too; disabling here means the
                        // customer is not offered a document built from figures
                        // that do not describe their project.
                        disabled={Boolean(busy) || !analysis || analysisStatus !== "complete"}
                        testId="create-proposal"
                      >
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
