"use client";

import dynamic from "next/dynamic";
import { Layers, Loader2, Ruler, Satellite, Square, Sun } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import type Konva from "konva";

import {
  Card,
  SectionTitle,
  SourceBadge,
  cn,
} from "@/components/ui/primitives";
import { area, degrees, metres } from "@/lib/format";
import type { Analysis, MapConfig, RoofModel } from "@/types/api";
import type { LayerToggles } from "./RoofStage";

// Konva touches `window` at import time, so the stage only ever renders client-side.
const RoofStage = dynamic(
  () => import("./RoofStage").then((m) => m.RoofStage),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[520px] items-center justify-center rounded-[10px] bg-[#0a1421] text-white/70">
        <Loader2 className="size-4 animate-spin" />
        <span className="ml-2 text-xs">Loading roof workspace…</span>
      </div>
    ),
  },
);

const TOGGLE_META = [
  { key: "satellite", label: "Satellite", icon: Satellite },
  { key: "facets", label: "Facets", icon: Layers },
  { key: "edges", label: "Edges", icon: Square },
  { key: "measurements", label: "Measurements", icon: Ruler },
  { key: "panels", label: "Panels", icon: Sun },
] as const;

export function RoofWorkspace({
  roof,
  analysis,
  mapConfig,
  onStageReady,
  onCaptureReady,
  busy,
}: {
  roof: RoofModel | null;
  analysis: Analysis | null;
  mapConfig: MapConfig | null;
  onStageReady?: (stage: Konva.Stage | null) => void;
  onCaptureReady?: (capture: () => Promise<string | null>) => void;
  busy?: string | null;
}) {
  const [toggles, setToggles] = useState<LayerToggles>({
    satellite: true,
    facets: true,
    edges: true,
    measurements: true,
    panels: true,
  });
  const [selectedFacetId, setSelectedFacetId] = useState<string | null>(null);

  const toggle = useCallback((key: keyof LayerToggles) => {
    setToggles((current) => ({ ...current, [key]: !current[key] }));
  }, []);

  const panelsByFacet = useMemo(() => {
    const map = new Map<string, number>();
    analysis?.layout.facets.forEach((f) => map.set(f.facetId, f.panelCount));
    return map;
  }, [analysis]);

  const selected = roof?.facets.find((f) => f.id === selectedFacetId) ?? null;

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-line px-3.5 py-2.5">
        {/* Wraps rather than overflowing: the row is 428 px wide and a phone
            is 412, so without this the last toggle is off-screen and cannot be
            pressed at all. */}
        <div className="flex flex-wrap items-center gap-1.5">
          {TOGGLE_META.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              data-testid={`layer-toggle-${key}`}
              onClick={() => toggle(key)}
              aria-pressed={toggles[key]}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium transition-colors",
                toggles[key]
                  ? "bg-navy-900 text-white"
                  : "border border-slate-line text-slate-body hover:bg-surface-2",
              )}
            >
              <Icon className="size-3.5" aria-hidden />
              {label}
            </button>
          ))}
        </div>
        {mapConfig ? (
          <SourceBadge
            testId="imagery-source-badge"
            tone={mapConfig.isLive ? "live" : "fixture"}
            label={mapConfig.isLive ? "Live imagery" : "Demo imagery"}
          />
        ) : null}
      </div>

      <div className="relative" data-testid="roof-stage">
        <RoofStage
          roof={roof}
          analysis={analysis}
          mapConfig={mapConfig}
          toggles={toggles}
          selectedFacetId={selectedFacetId}
          onSelectFacet={setSelectedFacetId}
          onStageReady={onStageReady}
          onCaptureReady={onCaptureReady}
        />
        {busy ? (
          <div
            role="status"
            aria-live="polite"
            data-testid="analysis-busy"
            className="absolute inset-0 flex items-center justify-center bg-[#0a1421]/70 backdrop-blur-[1px]"
          >
            <div className="flex items-center gap-2 rounded-lg bg-surface px-3.5 py-2 text-[13px] shadow">
              <Loader2
                className="size-4 animate-spin text-navy-700"
                aria-hidden
              />
              {busy}
            </div>
          </div>
        ) : null}
      </div>

      {roof ? (
        <div className="border-t border-slate-line px-3.5 py-3">
          <SectionTitle
            action={
              <span className="text-[11.5px] text-slate-muted">
                {metres(roof.groundMetresPerSourcePixel, 4)}/px · pitch{" "}
                {degrees(roof.pitchDeg, 0)}
              </span>
            }
          >
            Roof facets
          </SectionTitle>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {roof.facets.map((facet) => {
              const count = panelsByFacet.get(facet.id) ?? 0;
              const active = selectedFacetId === facet.id;
              return (
                <button
                  key={facet.id}
                  type="button"
                  data-testid={`facet-card-${facet.id}`}
                  data-panel-count={count}
                  aria-pressed={active}
                  onClick={() => setSelectedFacetId(active ? null : facet.id)}
                  className={cn(
                    "rounded-lg border px-2.5 py-2 text-left transition-colors",
                    active
                      ? "border-navy-700 bg-[#f2f7fc]"
                      : "border-slate-line hover:bg-surface-2",
                  )}
                >
                  <div className="text-[12px] font-semibold text-slate-ink">
                    {facet.label}
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-muted">
                    {degrees(facet.compassAzimuthDeg)} ·{" "}
                    {area(facet.slopedAreaM2)}
                  </div>
                  <div
                    className={cn(
                      "mt-1 text-[11.5px] font-medium",
                      count > 0 ? "text-navy-800" : "text-slate-muted",
                    )}
                  >
                    {count > 0 ? `${count} panels` : "No panels"}
                  </div>
                </button>
              );
            })}
          </div>

          {selected ? (
            <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[12px] text-slate-body">
              <strong className="font-semibold text-slate-ink">
                {selected.label}
              </strong>{" "}
              · compass {degrees(selected.compassAzimuthDeg)} · PVGIS aspect{" "}
              {degrees(selected.pvgisAspectDeg)} · plan{" "}
              {area(selected.projectedAreaM2)} · sloped{" "}
              {area(selected.slopedAreaM2)}
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
