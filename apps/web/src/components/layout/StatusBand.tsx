"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Sun } from "lucide-react";

import { api } from "@/lib/api";
import { dataSourceLabel } from "@/lib/format";
import type { HealthReady, MapConfig } from "@/types/api";

/**
 * The provenance band across the top of every operator page.
 *
 * It used to live inside the workspace, which meant the one thing this
 * application insists on — that you can always see whether a figure came from
 * a live source or a fixture — was visible on exactly one screen. A proposal
 * reviewed on the project page gave no such signal at all.
 *
 * Each chip reports **configuration**, not a live probe: the readiness endpoint
 * makes no outbound call, so rendering this costs one cached request and never
 * puts a third party on the page's critical path.
 *
 * The FX chip is per-proposal rather than global, so it stays where the figures
 * are and is passed in by the page that has them.
 */
/**
 * Lets a page add a chip of its own to the shared band.
 *
 * The FX chip is the reason this exists: it describes *one proposal's*
 * exchange rate, not the deployment's configuration, so it belongs to the page
 * that has the figures — but visually it belongs in the band beside the others.
 */
const ExtraChip = createContext<(node: ReactNode) => void>(() => {});

export function StatusBandProvider({ children }: { children: ReactNode }) {
  const [extra, setExtra] = useState<ReactNode>(null);
  return (
    <ExtraChip.Provider value={setExtra}>
      <StatusBand>{extra}</StatusBand>
      {children}
    </ExtraChip.Provider>
  );
}

/** Publish a chip into the band for as long as this component is mounted. */
export function useStatusChip(node: ReactNode): void {
  const setExtra = useContext(ExtraChip);
  useEffect(() => {
    setExtra(node);
    return () => setExtra(null);
  }, [node, setExtra]);
}

export function StatusBand({ children }: { children?: React.ReactNode }) {
  const [health, setHealth] = useState<HealthReady | null>(null);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.ready(), api.mapConfig()])
      .then(([ready, config]) => {
        if (cancelled) return;
        setHealth(ready);
        setMapConfig(config);
      })
      // A band that cannot describe the configuration simply shows less. It
      // must never be the reason a page fails to render.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const llm = health?.checks?.llm;
  const email = health?.checks?.email;

  return (
    <header className="sticky top-0 z-20 border-b border-slate-line bg-navy-900 text-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Sun className="size-5 text-solar-500" aria-hidden />
          <span className="text-[15px] font-semibold tracking-tight">
            solarVis AI
          </span>
          <span className="ml-2 hidden text-[12px] text-white/55 sm:inline">
            AI-powered solar proposal flow
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
          {llm ? (
            <Chip
              testId="status-parser"
              data={{ provider: llm.provider ?? "rules" }}
            >
              Parser:{" "}
              {llm.provider === "ollama"
                ? `Ollama · ${llm.model}`
                : "deterministic rules"}
            </Chip>
          ) : null}

          {mapConfig ? (
            <Chip
              testId="status-imagery"
              data={{ mode: mapConfig.isLive ? "live" : "stub" }}
            >
              Imagery: {mapConfig.isLive ? "live Google" : "test stub"}
            </Chip>
          ) : null}

          {email ? (
            // `sends` rather than `ready`: console mode is perfectly ready and
            // sends nothing, and that distinction is the one an operator has to
            // be able to see before they tell a customer it was emailed.
            <Chip
              testId="status-email"
              data={{
                provider: email.provider ?? "",
                sends: String(email.sends ?? false),
              }}
            >
              Email:{" "}
              {email.sends
                ? `live ${email.provider}`
                : "console — records only"}
            </Chip>
          ) : null}

          {children}
        </div>
      </div>
    </header>
  );
}

/** The FX chip, rendered by whichever page is showing figures. */
export function FxChip({ retrievalSource }: { retrievalSource: string }) {
  return (
    <Chip testId="status-fx" data={{ source: retrievalSource }}>
      FX: {dataSourceLabel(retrievalSource).label}
    </Chip>
  );
}

function Chip({
  children,
  testId,
  data,
}: {
  children: React.ReactNode;
  testId: string;
  data: Record<string, string>;
}) {
  const attributes = Object.fromEntries(
    Object.entries(data).map(([key, value]) => [`data-${key}`, value]),
  );
  return (
    <span
      data-testid={testId}
      {...attributes}
      className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5"
    >
      {children}
    </span>
  );
}
