"use client";

import dynamic from "next/dynamic";
import {
  AlertTriangle,
  Check,
  ClipboardCopy,
  Crosshair,
  Download,
  Hand,
  MousePointer2,
  RotateCcw,
  Spline,
  Square,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type CalEdge,
  type CalFacet,
  type CalVertex,
  type CalibrationState,
  EDGE_COLOUR,
  type EdgeType,
  MODE_HELP,
  type Mode,
  edgeLengthM,
  facetAreaM2,
  fromCalibrationJson,
  toCalibrationJson,
  validate,
} from "@/components/roof/calibrationTypes";
import { Button, Card, cn } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { MapConfig, RoofModel } from "@/types/api";

const CalibrationCanvas = dynamic(
  () => import("@/components/roof/CalibrationCanvas").then((m) => m.CalibrationCanvas),
  { ssr: false, loading: () => <div className="h-[640px] rounded-[10px] bg-[#0a1421]" /> },
);

const EMPTY: CalibrationState = { vertices: [], edges: [], facets: [] };

/**
 * Developer roof-calibration tool.
 *
 * Editing happens in source-map pixel coordinates, which is the only space the
 * committed calibration is defined in. Production users never see this page —
 * it exists so a human can inspect and correct what the derivation script
 * produced, against the actual imagery.
 */
export default function RoofCalibrationPage() {
  const [state, setState] = useState<CalibrationState>(EMPTY);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);
  const [mode, setMode] = useState<Mode>("select");
  const [edgeType, setEdgeType] = useState<EdgeType>("eave");
  const [selectedVertexIds, setSelectedVertexIds] = useState<string[]>([]);
  const [selectedFacetId, setSelectedFacetId] = useState<string | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [jsonDraft, setJsonDraft] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [showLayers, setShowLayers] = useState({
    image: true,
    vertices: true,
    edges: true,
    facets: true,
    labels: true,
  });

  const metresPerPixel = mapConfig?.groundMetresPerSourcePixel ?? 0;
  const vertexById = useMemo(() => new Map(state.vertices.map((v) => [v.id, v])), [state.vertices]);
  const problems = useMemo(() => validate(state), [state]);

  const loadCommitted = useCallback(async () => {
    try {
      const roof: RoofModel = await api.roofModel();
      setState({
        vertices: roof.vertices.map((v) => ({
          id: v.id,
          x: v.sourcePixel.x,
          y: v.sourcePixel.y,
        })),
        edges: roof.edgeGeometry.map((e) => ({
          id: e.id,
          startVertexId: e.startVertexId,
          endVertexId: e.endVertexId,
          edgeType: e.type,
        })),
        facets: roof.facetGeometry.map((f) => ({
          id: f.id,
          label: f.label,
          vertexIds: f.vertexIds,
          eaveEdgeId: f.eaveEdgeId,
        })),
      });
      setMessage({ tone: "ok", text: "Loaded the committed calibration." });
    } catch (caught) {
      setMessage({
        tone: "error",
        text: caught instanceof Error ? caught.message : "Could not load the calibration.",
      });
    }
  }, []);

  useEffect(() => {
    api.mapConfig().then(setMapConfig).catch(() => undefined);
    void loadCommitted();
  }, [loadCommitted]);

  // ----- editing -----------------------------------------------------------

  const addVertex = useCallback((x: number, y: number) => {
    setState((current) => {
      let n = current.vertices.length;
      let id = `v_${n}`;
      while (current.vertices.some((v) => v.id === id)) {
        n += 1;
        id = `v_${n}`;
      }
      return { ...current, vertices: [...current.vertices, { id, x, y }] };
    });
  }, []);

  const moveVertex = useCallback((id: string, x: number, y: number) => {
    setState((current) => ({
      ...current,
      vertices: current.vertices.map((v) => (v.id === id ? { ...v, x, y } : v)),
    }));
  }, []);

  const pickVertex = useCallback(
    (id: string) => {
      setSelectedVertexIds((current) => {
        if (mode === "select") return current.includes(id) ? [] : [id];

        const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];

        if (mode === "edge" && next.length === 2) {
          const [a, b] = next as [string, string];
          setState((s) => {
            const exists = s.edges.some(
              (e) =>
                (e.startVertexId === a && e.endVertexId === b) ||
                (e.startVertexId === b && e.endVertexId === a),
            );
            if (exists) return s;
            const id_ = `${edgeType}_${s.edges.filter((e) => e.edgeType === edgeType).length}`;
            return {
              ...s,
              edges: [...s.edges, { id: id_, startVertexId: a, endVertexId: b, edgeType }],
            };
          });
          return [];
        }
        return next;
      });
    },
    [mode, edgeType],
  );

  const closeFacet = useCallback(() => {
    if (selectedVertexIds.length < 3) {
      setMessage({ tone: "error", text: "A facet needs at least 3 vertices." });
      return;
    }
    setState((current) => {
      const id = `facet_${current.facets.length}`;
      return {
        ...current,
        facets: [
          ...current.facets,
          { id, label: `Facet ${current.facets.length + 1}`, vertexIds: [...selectedVertexIds], eaveEdgeId: null },
        ],
      };
    });
    setSelectedVertexIds([]);
    setMessage({ tone: "ok", text: "Facet created. Now assign its eave edge." });
  }, [selectedVertexIds]);

  const deleteSelected = useCallback(() => {
    if (selectedVertexIds.length === 0) return;
    const doomed = new Set(selectedVertexIds);
    setState((current) => ({
      vertices: current.vertices.filter((v) => !doomed.has(v.id)),
      // An edge or facet referencing a deleted vertex would be corrupt data,
      // so drop it rather than leave a dangling reference.
      edges: current.edges.filter(
        (e) => !doomed.has(e.startVertexId) && !doomed.has(e.endVertexId),
      ),
      facets: current.facets.filter((f) => !f.vertexIds.some((id) => doomed.has(id))),
    }));
    setSelectedVertexIds([]);
  }, [selectedVertexIds]);

  const renameVertex = useCallback((oldId: string, newId: string) => {
    const trimmed = newId.trim();
    if (!trimmed) return;
    setState((current) => {
      if (current.vertices.some((v) => v.id === trimmed && v.id !== oldId)) return current;
      return {
        vertices: current.vertices.map((v) => (v.id === oldId ? { ...v, id: trimmed } : v)),
        edges: current.edges.map((e) => ({
          ...e,
          startVertexId: e.startVertexId === oldId ? trimmed : e.startVertexId,
          endVertexId: e.endVertexId === oldId ? trimmed : e.endVertexId,
        })),
        facets: current.facets.map((f) => ({
          ...f,
          vertexIds: f.vertexIds.map((id) => (id === oldId ? trimmed : id)),
        })),
      };
    });
  }, []);

  // ----- JSON --------------------------------------------------------------

  const exported = useMemo(
    () =>
      JSON.stringify(
        toCalibrationJson(state, {
          sourceWidthPx: mapConfig?.sourceWidthPx ?? 1280,
          sourceHeightPx: mapConfig?.sourceHeightPx ?? 1280,
          metresPerPixel,
          pitchDeg: 25,
        }),
        null,
        2,
      ),
    [state, mapConfig, metresPerPixel],
  );

  const copyJson = useCallback(async () => {
    await navigator.clipboard.writeText(exported);
    setMessage({ tone: "ok", text: "Calibration JSON copied to the clipboard." });
  }, [exported]);

  const downloadJson = useCallback(() => {
    const blob = new Blob([exported], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "fixed_roof_calibration.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }, [exported]);

  const importJson = useCallback(() => {
    try {
      setState(fromCalibrationJson(JSON.parse(jsonDraft)));
      setSelectedVertexIds([]);
      setMessage({ tone: "ok", text: "Imported." });
    } catch (caught) {
      setMessage({
        tone: "error",
        text: caught instanceof Error ? caught.message : "Could not parse that JSON.",
      });
    }
  }, [jsonDraft]);

  // ----- render ------------------------------------------------------------

  const modes: { key: Mode; label: string; icon: typeof MousePointer2 }[] = [
    { key: "select", label: "Select", icon: MousePointer2 },
    { key: "add", label: "Add vertex", icon: Crosshair },
    { key: "edge", label: "Draw edge", icon: Spline },
    { key: "facet", label: "Build facet", icon: Square },
    { key: "pan", label: "Pan", icon: Hand },
  ];

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-line bg-navy-900 px-4 py-2.5 text-white">
        <div className="mx-auto flex max-w-[1700px] items-center justify-between gap-3">
          <div>
            <div className="text-[14px] font-semibold">Roof calibration</div>
            <div className="text-[11px] text-white/55">
              Developer tool · coordinates are source-map pixels on the{" "}
              {mapConfig?.sourceWidthPx ?? 1280}×{mapConfig?.sourceHeightPx ?? 1280} raster
            </div>
          </div>
          <div className="flex items-center gap-3 text-[11.5px]">
            {mapConfig ? (
              <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5">
                {metresPerPixel.toFixed(6)} m/px · zoom {mapConfig.zoom} · scale {mapConfig.scale}
              </span>
            ) : null}
            <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5 tabular-nums">
              {cursor ? `x ${cursor.x.toFixed(1)}  y ${cursor.y.toFixed(1)}` : "—"}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1700px] gap-4 px-4 py-4 xl:grid-cols-[1fr_400px]">
        <div className="space-y-3">
          <Card className="p-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
              {modes.map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => {
                    setMode(key);
                    setSelectedVertexIds([]);
                  }}
                  aria-pressed={mode === key}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium",
                    mode === key
                      ? "bg-navy-900 text-white"
                      : "border border-slate-line text-slate-body hover:bg-surface-2",
                  )}
                >
                  <Icon className="size-3.5" aria-hidden />
                  {label}
                </button>
              ))}

              <span className="mx-1 h-5 w-px bg-slate-line" />

              {(["eave", "hip", "ridge"] as EdgeType[]).map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setEdgeType(type)}
                  aria-pressed={edgeType === type}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12px] capitalize",
                    edgeType === type
                      ? "border-navy-700 bg-[#eef4fa] font-medium text-navy-900"
                      : "border-slate-line text-slate-body hover:bg-surface-2",
                  )}
                >
                  <span
                    aria-hidden
                    className="block h-[3px] w-3.5 rounded"
                    style={{ background: EDGE_COLOUR[type] , outline: "1px solid #c3c2b7" }}
                  />
                  {type}
                </button>
              ))}

              <span className="mx-1 h-5 w-px bg-slate-line" />

              <Button variant="secondary" onClick={closeFacet} disabled={mode !== "facet"}>
                Close facet
              </Button>
              <Button
                variant="secondary"
                onClick={deleteSelected}
                disabled={selectedVertexIds.length === 0}
              >
                <Trash2 className="size-3.5" /> Delete
              </Button>
              <Button
                variant="secondary"
                onClick={() =>
                  (window as unknown as { __calibrationFit?: () => void }).__calibrationFit?.()
                }
              >
                Fit
              </Button>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-slate-line pt-2 text-[11.5px]">
              <span className="text-slate-muted">{MODE_HELP[mode]}</span>
              <span className="ml-auto flex items-center gap-2">
                {(Object.keys(showLayers) as (keyof typeof showLayers)[]).map((layer) => (
                  <label key={layer} className="flex items-center gap-1 capitalize text-slate-body">
                    <input
                      type="checkbox"
                      checked={showLayers[layer]}
                      onChange={() =>
                        setShowLayers((current) => ({ ...current, [layer]: !current[layer] }))
                      }
                    />
                    {layer}
                  </label>
                ))}
              </span>
            </div>
          </Card>

          <Card className="overflow-hidden">
            <CalibrationCanvas
              mapConfig={mapConfig}
              vertices={state.vertices}
              edges={state.edges}
              facets={state.facets}
              mode={mode}
              edgeType={edgeType}
              selectedVertexIds={selectedVertexIds}
              selectedFacetId={selectedFacetId}
              showLayers={showLayers}
              onAddVertex={addVertex}
              onMoveVertex={moveVertex}
              onPickVertex={pickVertex}
              onPickFacet={setSelectedFacetId}
              onCursor={setCursor}
            />
          </Card>

          {message ? (
            <div
              className={cn(
                "rounded-lg px-3 py-2 text-[12.5px]",
                message.tone === "ok"
                  ? "bg-[#e8f6ec] text-good-700"
                  : "bg-[#fdeeee] text-[#8a1f1f]",
              )}
            >
              {message.text}
            </div>
          ) : null}
        </div>

        <div className="space-y-3">
          <Card className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[13px] font-semibold">Validation</h2>
              {problems.length === 0 ? (
                <span className="flex items-center gap-1 text-[12px] text-good-700">
                  <Check className="size-3.5" /> Ready to commit
                </span>
              ) : (
                <span className="flex items-center gap-1 text-[12px] text-[#8a5210]">
                  <AlertTriangle className="size-3.5" /> {problems.length} issue
                  {problems.length === 1 ? "" : "s"}
                </span>
              )}
            </div>
            {problems.length > 0 ? (
              <ul className="space-y-0.5 text-[11.5px] text-slate-body">
                {problems.map((problem) => (
                  <li key={problem}>· {problem}</li>
                ))}
              </ul>
            ) : (
              <p className="text-[11.5px] text-slate-muted">
                4 facets, every facet has an eave, all edge references resolve.
              </p>
            )}
          </Card>

          <Card className="p-3">
            <h2 className="mb-2 text-[13px] font-semibold">
              Vertices <span className="text-slate-muted">({state.vertices.length})</span>
            </h2>
            <div className="scroll-thin max-h-52 space-y-1 overflow-y-auto">
              {state.vertices.map((vertex) => (
                <div
                  key={vertex.id}
                  className={cn(
                    "flex items-center gap-2 rounded border px-2 py-1 text-[11.5px]",
                    selectedVertexIds.includes(vertex.id)
                      ? "border-navy-700 bg-[#f2f7fc]"
                      : "border-slate-line",
                  )}
                >
                  <input
                    className="w-28 rounded border border-slate-line px-1 py-0.5 text-[11px]"
                    defaultValue={vertex.id}
                    onBlur={(e) => renameVertex(vertex.id, e.target.value)}
                    aria-label={`Name of ${vertex.id}`}
                  />
                  <span className="tabular-nums text-slate-muted">
                    {vertex.x.toFixed(2)}, {vertex.y.toFixed(2)}
                  </span>
                  <button
                    type="button"
                    className="ml-auto text-navy-700 hover:underline"
                    onClick={() => pickVertex(vertex.id)}
                  >
                    select
                  </button>
                </div>
              ))}
              {state.vertices.length === 0 ? (
                <p className="text-[11.5px] text-slate-muted">No vertices yet.</p>
              ) : null}
            </div>
          </Card>

          <Card className="p-3">
            <h2 className="mb-2 text-[13px] font-semibold">
              Edges <span className="text-slate-muted">({state.edges.length})</span>
            </h2>
            <div className="scroll-thin max-h-52 space-y-1 overflow-y-auto">
              {state.edges.map((edge) => {
                const length = edgeLengthM(
                  vertexById.get(edge.startVertexId),
                  vertexById.get(edge.endVertexId),
                  metresPerPixel,
                );
                return (
                  <div
                    key={edge.id}
                    className="flex items-center gap-2 rounded border border-slate-line px-2 py-1 text-[11.5px]"
                  >
                    <span
                      aria-hidden
                      className="block h-[3px] w-3"
                      style={{ background: EDGE_COLOUR[edge.edgeType], outline: "1px solid #c3c2b7" }}
                    />
                    <span className="w-16 truncate font-medium">{edge.id}</span>
                    <select
                      className="rounded border border-slate-line px-1 py-0.5 text-[11px]"
                      value={edge.edgeType}
                      aria-label={`Type of ${edge.id}`}
                      onChange={(e) =>
                        setState((current) => ({
                          ...current,
                          edges: current.edges.map((x) =>
                            x.id === edge.id
                              ? { ...x, edgeType: e.target.value as EdgeType }
                              : x,
                          ),
                        }))
                      }
                    >
                      <option value="eave">eave</option>
                      <option value="hip">hip</option>
                      <option value="ridge">ridge</option>
                    </select>
                    <span className="ml-auto tabular-nums text-slate-muted">
                      {length !== null ? `${length.toFixed(3)} m` : "—"}
                    </span>
                  </div>
                );
              })}
              {state.edges.length === 0 ? (
                <p className="text-[11.5px] text-slate-muted">No edges yet.</p>
              ) : null}
            </div>
          </Card>

          <Card className="p-3">
            <h2 className="mb-2 text-[13px] font-semibold">
              Facets <span className="text-slate-muted">({state.facets.length})</span>
            </h2>
            <div className="space-y-1.5">
              {state.facets.map((facet) => {
                const areaValue = facetAreaM2(facet, vertexById, metresPerPixel);
                return (
                  <div
                    key={facet.id}
                    className={cn(
                      "rounded border px-2 py-1.5 text-[11.5px]",
                      selectedFacetId === facet.id
                        ? "border-navy-700 bg-[#f2f7fc]"
                        : "border-slate-line",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <input
                        className="w-32 rounded border border-slate-line px-1 py-0.5 text-[11px]"
                        defaultValue={facet.label}
                        aria-label={`Label of ${facet.id}`}
                        onBlur={(e) =>
                          setState((current) => ({
                            ...current,
                            facets: current.facets.map((f) =>
                              f.id === facet.id ? { ...f, label: e.target.value } : f,
                            ),
                          }))
                        }
                      />
                      <span className="tabular-nums text-slate-muted">
                        {areaValue !== null ? `${areaValue.toFixed(2)} m²` : "—"}
                      </span>
                      <button
                        type="button"
                        className="ml-auto text-[#8a1f1f] hover:underline"
                        onClick={() =>
                          setState((current) => ({
                            ...current,
                            facets: current.facets.filter((f) => f.id !== facet.id),
                          }))
                        }
                      >
                        remove
                      </button>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <span className="text-slate-muted">Eave:</span>
                      <select
                        className="flex-1 rounded border border-slate-line px-1 py-0.5 text-[11px]"
                        value={facet.eaveEdgeId ?? ""}
                        aria-label={`Eave edge of ${facet.id}`}
                        onChange={(e) =>
                          setState((current) => ({
                            ...current,
                            facets: current.facets.map((f) =>
                              f.id === facet.id
                                ? { ...f, eaveEdgeId: e.target.value || null }
                                : f,
                            ),
                          }))
                        }
                      >
                        <option value="">— choose an edge —</option>
                        {state.edges.map((edge) => (
                          <option key={edge.id} value={edge.id}>
                            {edge.id} ({edge.edgeType})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="mt-0.5 truncate text-[10.5px] text-slate-muted">
                      {facet.vertexIds.join(" → ")}
                    </div>
                  </div>
                );
              })}
              {state.facets.length === 0 ? (
                <p className="text-[11.5px] text-slate-muted">No facets yet.</p>
              ) : null}
            </div>
          </Card>

          <Card className="p-3">
            <h2 className="mb-2 text-[13px] font-semibold">Calibration JSON</h2>
            <div className="mb-2 flex flex-wrap gap-1.5">
              <Button variant="secondary" onClick={copyJson}>
                <ClipboardCopy className="size-3.5" /> Copy
              </Button>
              <Button variant="secondary" onClick={downloadJson}>
                <Download className="size-3.5" /> Download
              </Button>
              <Button variant="secondary" onClick={importJson} disabled={!jsonDraft.trim()}>
                <Upload className="size-3.5" /> Import
              </Button>
              <Button variant="secondary" onClick={loadCommitted}>
                <RotateCcw className="size-3.5" /> Reset to committed
              </Button>
            </div>
            <textarea
              className="scroll-thin h-40 w-full rounded border border-slate-line p-2 font-mono text-[10.5px]"
              placeholder="Paste calibration JSON here, then press Import…"
              value={jsonDraft}
              onChange={(e) => setJsonDraft(e.target.value)}
              aria-label="Calibration JSON to import"
            />
            <details className="mt-2">
              <summary className="cursor-pointer text-[11.5px] text-navy-800">
                Current export
              </summary>
              <pre className="scroll-thin mt-1 max-h-52 overflow-auto rounded bg-surface-2 p-2 font-mono text-[10px]">
                {exported}
              </pre>
            </details>
            <p className="mt-2 text-[11px] text-slate-muted">
              Commit by replacing{" "}
              <code>apps/api/app/data/fixed_roof_calibration.json</code>. Coordinates are
              source-map pixels, so they stay valid when live imagery replaces the fixture.
            </p>
          </Card>
        </div>
      </main>
    </div>
  );
}
