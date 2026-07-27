"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from "react-konva";
import type Konva from "konva";

import type { Analysis, MapConfig, Point, RoofModel } from "@/types/api";

/**
 * The roof workspace.
 *
 * Everything the API sends is in **source-map pixels** — the canonical
 * 1280×1280 raster. This component multiplies by a single display factor on
 * the way out and never stores a screen coordinate, which is what keeps the
 * overlay aligned when the viewport resizes.
 *
 * The satellite image is same-origin (proxied by the backend), so the stage
 * can be exported to PNG without tainting the canvas.
 */

export type LayerToggles = {
  satellite: boolean;
  facets: boolean;
  edges: boolean;
  measurements: boolean;
  panels: boolean;
};

const EDGE_STYLE = {
  eave: { stroke: "#ffffff", width: 2.4, label: "Eave" },
  hip: { stroke: "#6ed7ff", width: 2, label: "Hip" },
  ridge: { stroke: "#ffc337", width: 2.4, label: "Ridge" },
} as const;

const FACET_FILL = "rgba(42,120,214,0.13)";
const FACET_FILL_ACTIVE = "rgba(42,120,214,0.3)";
const FACET_STROKE = "rgba(255,255,255,0.55)";
const PANEL_FILL = "#123a63";
const PANEL_STROKE = "#8fc2f2";

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

export function RoofStage({
  roof,
  analysis,
  mapConfig,
  toggles,
  selectedFacetId,
  onSelectFacet,
  onStageReady,
  height = 520,
}: {
  roof: RoofModel | null;
  analysis: Analysis | null;
  mapConfig: MapConfig | null;
  toggles: LayerToggles;
  selectedFacetId: string | null;
  onSelectFacet?: (facetId: string | null) => void;
  onStageReady?: (stage: Konva.Stage | null) => void;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<Konva.Stage>(null);
  const [width, setWidth] = useState(720);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });

  const sourceSize = mapConfig?.sourceWidthPx ?? roof?.sourceWidthPx ?? 1280;

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!mapConfig) return;
    const img = new window.Image();
    // Same-origin, so no crossOrigin dance and no tainted canvas on export.
    img.src = mapConfig.imageUrl;
    img.onload = () => setImage(img);
    return () => {
      img.onload = null;
    };
  }, [mapConfig]);

  useEffect(() => {
    onStageReady?.(stageRef.current);
  }, [onStageReady, image]);

  // One factor maps source pixels to the rendered stage.
  const displayScale = width / sourceSize;
  const stageHeight = height;

  const project = useCallback(
    (p: Point) => ({ x: p.x * displayScale, y: p.y * displayScale }),
    [displayScale],
  );

  const vertices = useMemo(() => {
    const map = new Map<string, Point>();
    roof?.vertices.forEach((v) => map.set(v.id, v.sourcePixel));
    return map;
  }, [roof]);

  const panelsByFacet = useMemo(() => {
    const map = new Map<string, Point[][]>();
    analysis?.layout.facets.forEach((facet) => {
      map.set(
        facet.facetId,
        facet.panels.map((panel) => panel.sourcePixelPolygon),
      );
    });
    return map;
  }, [analysis]);

  const metresPerPixel = roof?.groundMetresPerSourcePixel ?? 0;

  /** Centre the roof and zoom so it fills the frame comfortably. */
  const fitToRoof = useCallback(() => {
    if (!roof || roof.facetGeometry.length === 0) return;
    const points = roof.facetGeometry.flatMap((f) => f.sourcePixelPolygon);
    const xs = points.map((p) => p.x * displayScale);
    const ys = points.map((p) => p.y * displayScale);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const pad = 40;
    const scale = Math.min(
      (width - pad * 2) / Math.max(maxX - minX, 1),
      (stageHeight - pad * 2) / Math.max(maxY - minY, 1),
    );
    const clamped = Math.min(Math.max(scale, 0.4), 6);
    setView({
      scale: clamped,
      x: width / 2 - ((minX + maxX) / 2) * clamped,
      y: stageHeight / 2 - ((minY + maxY) / 2) * clamped,
    });
  }, [roof, displayScale, width, stageHeight]);

  useEffect(() => {
    if (roof && image) fitToRoof();
  }, [roof, image, fitToRoof]);

  const handleWheel = useCallback((event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    const stage = event.target.getStage();
    if (!stage) return;
    const pointer = stage.getPointerPosition();
    if (!pointer) return;

    setView((current) => {
      const next = Math.min(
        Math.max(current.scale * (event.evt.deltaY > 0 ? 0.9 : 1.1), 0.3),
        10,
      );
      const worldX = (pointer.x - current.x) / current.scale;
      const worldY = (pointer.y - current.y) / current.scale;
      return { scale: next, x: pointer.x - worldX * next, y: pointer.y - worldY * next };
    });
  }, []);

  const scaleBar = useMemo(() => {
    if (!metresPerPixel) return null;
    const targetPx = 90;
    const metres = targetPx / (displayScale * view.scale) * metresPerPixel;
    const nice = [1, 2, 5, 10, 20, 50].find((n) => n >= metres) ?? 100;
    return { metres: nice, px: (nice / metresPerPixel) * displayScale * view.scale };
  }, [metresPerPixel, displayScale, view.scale]);

  return (
    <div ref={containerRef} className="relative w-full" style={{ height: stageHeight }}>
      <Stage
        ref={stageRef}
        width={width}
        height={stageHeight}
        scaleX={view.scale}
        scaleY={view.scale}
        x={view.x}
        y={view.y}
        draggable
        onWheel={handleWheel}
        onDragEnd={(e) => setView((v) => ({ ...v, x: e.target.x(), y: e.target.y() }))}
        onClick={(e) => {
          if (e.target === e.target.getStage()) onSelectFacet?.(null);
        }}
        style={{ background: "#0a1421", borderRadius: 10, cursor: "grab" }}
      >
        <Layer listening={false}>
          {toggles.satellite && image ? (
            <KonvaImage
              image={image}
              width={sourceSize * displayScale}
              height={sourceSize * displayScale}
            />
          ) : (
            <Rect width={sourceSize * displayScale} height={sourceSize * displayScale} fill="#0f1b2b" />
          )}
        </Layer>

        {toggles.facets && roof ? (
          <Layer>
            {roof.facetGeometry.map((facet) => {
              const flat = facet.sourcePixelPolygon.flatMap((p) => {
                const q = project(p);
                return [q.x, q.y];
              });
              const active = selectedFacetId === facet.id;
              return (
                <Line
                  key={facet.id}
                  points={flat}
                  closed
                  fill={active ? FACET_FILL_ACTIVE : FACET_FILL}
                  stroke={FACET_STROKE}
                  strokeWidth={1 / view.scale}
                  onClick={() => onSelectFacet?.(active ? null : facet.id)}
                  onTap={() => onSelectFacet?.(active ? null : facet.id)}
                  onMouseEnter={(e) => {
                    const container = e.target.getStage()?.container();
                    if (container) container.style.cursor = "pointer";
                  }}
                  onMouseLeave={(e) => {
                    const container = e.target.getStage()?.container();
                    if (container) container.style.cursor = "grab";
                  }}
                />
              );
            })}
          </Layer>
        ) : null}

        {toggles.panels && analysis ? (
          <Layer listening={false}>
            {[...panelsByFacet.entries()].flatMap(([facetId, panels]) =>
              panels.map((polygon, index) => {
                const flat = polygon.flatMap((p) => {
                  const q = project(p);
                  return [q.x, q.y];
                });
                return (
                  <Line
                    key={`${facetId}-${index}`}
                    points={flat}
                    closed
                    fill={PANEL_FILL}
                    opacity={0.92}
                    stroke={PANEL_STROKE}
                    strokeWidth={0.8 / view.scale}
                  />
                );
              }),
            )}
          </Layer>
        ) : null}

        {toggles.edges && roof ? (
          <Layer listening={false}>
            {roof.edgeGeometry.map((edge) => {
              const a = vertices.get(edge.startVertexId);
              const b = vertices.get(edge.endVertexId);
              if (!a || !b) return null;
              const style = EDGE_STYLE[edge.type];
              const pa = project(a);
              const pb = project(b);
              return (
                <Line
                  key={edge.id}
                  points={[pa.x, pa.y, pb.x, pb.y]}
                  stroke={style.stroke}
                  strokeWidth={style.width / view.scale}
                  lineCap="round"
                  shadowColor="#000"
                  shadowBlur={3 / view.scale}
                  shadowOpacity={0.5}
                />
              );
            })}
            {roof.vertices.map((vertex) => {
              const p = project(vertex.sourcePixel);
              return (
                <Circle
                  key={vertex.id}
                  x={p.x}
                  y={p.y}
                  radius={2.6 / view.scale}
                  fill="#ffffff"
                  stroke="#0a1421"
                  strokeWidth={1 / view.scale}
                />
              );
            })}
          </Layer>
        ) : null}

        {toggles.measurements && roof ? (
          <Layer listening={false}>
            {roof.edgeGeometry.map((edge) => {
              const a = vertices.get(edge.startVertexId);
              const b = vertices.get(edge.endVertexId);
              if (!a || !b) return null;
              const mid = project(midpoint(a, b));
              const label = `${edge.projectedLengthM.toFixed(2)} m`;
              const fontSize = 11 / view.scale;
              const padding = 3 / view.scale;
              const boxWidth = label.length * fontSize * 0.58 + padding * 2;
              const boxHeight = fontSize + padding * 2;
              return (
                <Group key={`m-${edge.id}`} x={mid.x - boxWidth / 2} y={mid.y - boxHeight / 2}>
                  <Rect
                    width={boxWidth}
                    height={boxHeight}
                    fill="rgba(10,20,33,0.82)"
                    cornerRadius={3 / view.scale}
                  />
                  <Text
                    text={label}
                    fontSize={fontSize}
                    fontFamily="system-ui, sans-serif"
                    fill="#ffffff"
                    width={boxWidth}
                    height={boxHeight}
                    align="center"
                    verticalAlign="middle"
                  />
                </Group>
              );
            })}
          </Layer>
        ) : null}

        {/* Attribution is part of the scene, so it is baked into any export. */}
        <Layer listening={false}>
          <Text
            text={mapConfig?.attribution ?? ""}
            x={6 / view.scale - view.x / view.scale}
            y={(stageHeight - 16) / view.scale - view.y / view.scale}
            fontSize={10 / view.scale}
            fontFamily="system-ui, sans-serif"
            fill="rgba(255,255,255,0.82)"
            shadowColor="#000"
            shadowBlur={3 / view.scale}
          />
        </Layer>
      </Stage>

      {scaleBar ? (
        <div className="pointer-events-none absolute bottom-2 right-3 flex items-center gap-2 rounded-md bg-black/55 px-2 py-1 text-[10px] text-white">
          <span
            className="block h-[3px] bg-white"
            style={{ width: `${Math.round(scaleBar.px)}px` }}
          />
          {scaleBar.metres} m
        </div>
      ) : null}

      <div className="pointer-events-none absolute left-3 top-2 flex gap-3 rounded-md bg-black/45 px-2 py-1 text-[10px] text-white">
        <LegendSwatch colour={EDGE_STYLE.eave.stroke} label="Eave" />
        <LegendSwatch colour={EDGE_STYLE.hip.stroke} label="Hip" />
        <LegendSwatch colour={EDGE_STYLE.ridge.stroke} label="Ridge" />
      </div>

      <button
        type="button"
        onClick={fitToRoof}
        className="absolute right-3 top-2 rounded-md bg-black/55 px-2 py-1 text-[11px] text-white hover:bg-black/70"
      >
        Fit
      </button>
    </div>
  );
}

function LegendSwatch({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="block h-[2px] w-3.5" style={{ background: colour }} />
      {label}
    </span>
  );
}
