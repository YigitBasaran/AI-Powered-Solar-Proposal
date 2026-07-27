"use client";

import type Konva from "konva";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from "react-konva";

import type { MapConfig } from "@/types/api";
import type { CalEdge, CalFacet, CalVertex, EdgeType, Mode } from "./calibrationTypes";
import { EDGE_COLOUR } from "./calibrationTypes";

/**
 * Editing surface for the roof calibration tool.
 *
 * Every click is converted to **source-map pixel** coordinates before it
 * touches state, so what is edited is what gets committed. Viewport pan and
 * zoom are a display concern and never leak into the data — which is the exact
 * class of bug that once offset the whole calibration by the window origin.
 */
export function CalibrationCanvas({
  mapConfig,
  vertices,
  edges,
  facets,
  mode,
  edgeType,
  selectedVertexIds,
  selectedFacetId,
  showLayers,
  onAddVertex,
  onMoveVertex,
  onPickVertex,
  onPickFacet,
  onCursor,
  height = 640,
}: {
  mapConfig: MapConfig | null;
  vertices: CalVertex[];
  edges: CalEdge[];
  facets: CalFacet[];
  mode: Mode;
  edgeType: EdgeType;
  selectedVertexIds: string[];
  selectedFacetId: string | null;
  showLayers: { image: boolean; vertices: boolean; edges: boolean; facets: boolean; labels: boolean };
  onAddVertex: (x: number, y: number) => void;
  onMoveVertex: (id: string, x: number, y: number) => void;
  onPickVertex: (id: string) => void;
  onPickFacet: (id: string | null) => void;
  onCursor: (p: { x: number; y: number } | null) => void;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<Konva.Stage>(null);
  const [width, setWidth] = useState(900);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });

  const source = mapConfig?.sourceWidthPx ?? 1280;

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!mapConfig) return;
    const img = new window.Image();
    img.src = mapConfig.imageUrl;
    img.onload = () => setImage(img);
  }, [mapConfig]);

  const base = width / source; // source px -> unzoomed stage px
  const vertexById = useMemo(() => new Map(vertices.map((v) => [v.id, v])), [vertices]);

  const toStage = useCallback((p: { x: number; y: number }) => ({ x: p.x * base, y: p.y * base }), [base]);

  /** Pointer position in SOURCE pixels, undoing pan and zoom. */
  const pointerSource = useCallback(
    (stage: Konva.Stage) => {
      const pointer = stage.getPointerPosition();
      if (!pointer) return null;
      return {
        x: (pointer.x - view.x) / view.scale / base,
        y: (pointer.y - view.y) / view.scale / base,
      };
    },
    [view, base],
  );

  const fit = useCallback(() => {
    if (vertices.length < 2) {
      setView({ scale: 1, x: 0, y: 0 });
      return;
    }
    const xs = vertices.map((v) => v.x * base);
    const ys = vertices.map((v) => v.y * base);
    const pad = 60;
    const scale = Math.min(
      (width - pad * 2) / Math.max(Math.max(...xs) - Math.min(...xs), 1),
      (height - pad * 2) / Math.max(Math.max(...ys) - Math.min(...ys), 1),
    );
    const clamped = Math.min(Math.max(scale, 0.3), 8);
    setView({
      scale: clamped,
      x: width / 2 - ((Math.min(...xs) + Math.max(...xs)) / 2) * clamped,
      y: height / 2 - ((Math.min(...ys) + Math.max(...ys)) / 2) * clamped,
    });
  }, [vertices, base, width, height]);

  useEffect(() => {
    (window as unknown as { __calibrationFit?: () => void }).__calibrationFit = fit;
  }, [fit]);

  const handleWheel = useCallback((event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    const stage = event.target.getStage();
    const pointer = stage?.getPointerPosition();
    if (!pointer) return;
    setView((current) => {
      const next = Math.min(Math.max(current.scale * (event.evt.deltaY > 0 ? 0.9 : 1.1), 0.2), 20);
      return {
        scale: next,
        x: pointer.x - ((pointer.x - current.x) / current.scale) * next,
        y: pointer.y - ((pointer.y - current.y) / current.scale) * next,
      };
    });
  }, []);

  return (
    <div ref={containerRef} className="relative w-full" style={{ height }}>
      <Stage
        ref={stageRef}
        width={width}
        height={height}
        scaleX={view.scale}
        scaleY={view.scale}
        x={view.x}
        y={view.y}
        draggable={mode === "pan"}
        onWheel={handleWheel}
        onDragEnd={(e) => setView((v) => ({ ...v, x: e.target.x(), y: e.target.y() }))}
        onMouseMove={(e) => {
          const stage = e.target.getStage();
          if (stage) onCursor(pointerSource(stage));
        }}
        onMouseLeave={() => onCursor(null)}
        onClick={(e) => {
          const stage = e.target.getStage();
          if (!stage) return;
          if (e.target === stage) {
            if (mode === "add") {
              const p = pointerSource(stage);
              if (p) onAddVertex(Math.round(p.x * 100) / 100, Math.round(p.y * 100) / 100);
            } else {
              onPickFacet(null);
            }
          }
        }}
        style={{
          background: "#0a1421",
          borderRadius: 10,
          cursor: mode === "add" ? "crosshair" : mode === "pan" ? "grab" : "default",
        }}
      >
        <Layer listening={false}>
          {showLayers.image && image ? (
            <KonvaImage image={image} width={source * base} height={source * base} />
          ) : (
            <Rect width={source * base} height={source * base} fill="#0f1b2b" />
          )}
        </Layer>

        {showLayers.facets ? (
          <Layer>
            {facets.map((facet) => {
              const points = facet.vertexIds
                .map((id) => vertexById.get(id))
                .filter((v): v is CalVertex => Boolean(v))
                .flatMap((v) => {
                  const p = toStage(v);
                  return [p.x, p.y];
                });
              if (points.length < 6) return null;
              const active = selectedFacetId === facet.id;
              return (
                <Line
                  key={facet.id}
                  points={points}
                  closed
                  fill={active ? "rgba(42,120,214,0.34)" : "rgba(42,120,214,0.15)"}
                  stroke="rgba(255,255,255,0.5)"
                  strokeWidth={1 / view.scale}
                  onClick={() => onPickFacet(active ? null : facet.id)}
                />
              );
            })}
          </Layer>
        ) : null}

        {showLayers.edges ? (
          <Layer listening={false}>
            {edges.map((edge) => {
              const a = vertexById.get(edge.startVertexId);
              const b = vertexById.get(edge.endVertexId);
              if (!a || !b) return null;
              const pa = toStage(a);
              const pb = toStage(b);
              return (
                <Line
                  key={edge.id}
                  points={[pa.x, pa.y, pb.x, pb.y]}
                  stroke={EDGE_COLOUR[edge.edgeType]}
                  strokeWidth={2.2 / view.scale}
                  lineCap="round"
                  shadowColor="#000"
                  shadowBlur={3 / view.scale}
                />
              );
            })}
          </Layer>
        ) : null}

        {showLayers.vertices ? (
          <Layer>
            {vertices.map((vertex) => {
              const p = toStage(vertex);
              const index = selectedVertexIds.indexOf(vertex.id);
              const selected = index >= 0;
              return (
                <Group key={vertex.id}>
                  <Circle
                    x={p.x}
                    y={p.y}
                    radius={(selected ? 7 : 5) / view.scale}
                    fill={selected ? "#ffc337" : "#ff4d4d"}
                    stroke="#0a1421"
                    strokeWidth={1.5 / view.scale}
                    draggable={mode === "select"}
                    onDragMove={(e) =>
                      onMoveVertex(
                        vertex.id,
                        Math.round((e.target.x() / base) * 100) / 100,
                        Math.round((e.target.y() / base) * 100) / 100,
                      )
                    }
                    onClick={() => onPickVertex(vertex.id)}
                    onTap={() => onPickVertex(vertex.id)}
                    onMouseEnter={(e) => {
                      const c = e.target.getStage()?.container();
                      if (c) c.style.cursor = mode === "select" ? "move" : "pointer";
                    }}
                    onMouseLeave={(e) => {
                      const c = e.target.getStage()?.container();
                      if (c) c.style.cursor = mode === "add" ? "crosshair" : "default";
                    }}
                  />
                  {showLayers.labels ? (
                    <Text
                      x={p.x + 8 / view.scale}
                      y={p.y - 6 / view.scale}
                      text={selected ? `${index + 1}. ${vertex.id}` : vertex.id}
                      fontSize={11 / view.scale}
                      fontFamily="system-ui, sans-serif"
                      fill="#ffffff"
                      shadowColor="#000"
                      shadowBlur={4 / view.scale}
                      listening={false}
                    />
                  ) : null}
                </Group>
              );
            })}
          </Layer>
        ) : null}
      </Stage>
    </div>
  );
}
