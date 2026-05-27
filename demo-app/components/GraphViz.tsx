"use client";

import { useMemo, useRef } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import type { Node, Relationship } from "@neo4j-nvl/base";
import { GraphContext } from "@/lib/search";

// ── Node colours by entity label ─────────────────────────────────────────────
const LABEL_COLOR: Record<string, string> = {
  Person:        "#3b82f6",   // blue-500
  Place:         "#f59e0b",   // amber-500
  WaterBody:     "#06b6d4",   // cyan-500
  AnimalSpecies: "#22c55e",   // green-500
  PlantSpecies:  "#84cc16",   // lime-500
  NativeNation:  "#a855f7",   // purple-500
  Taxon:         "#f97316",   // orange-500
};

const LEGEND = Object.entries(LABEL_COLOR).map(([label, color]) => ({ label, color }));

interface Props {
  graph: GraphContext;
}

export default function GraphViz({ graph }: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nvlRef = useRef<any>(null);

  const { nodes, rels } = useMemo(() => {
    // Deduplicate nodes by label:name key
    const nodeMap = new Map<string, Node>();
    for (const e of graph.entities) {
      if (!e.name) continue;
      const id = `${e.label}:${e.name}`;
      if (!nodeMap.has(id)) {
        const caption = e.name
          .toLowerCase()
          .replace(/\b\w/g, (c) => c.toUpperCase());
        nodeMap.set(id, {
          id,
          caption,
          captionAlign: "bottom",
          captionSize: 9,
          color: LABEL_COLOR[e.label] ?? "#6b7280",
          size: 28,
        });
      }
    }

    // Only include relationships where both endpoint nodes exist
    const relList: Relationship[] = [];
    const seen = new Set<string>();
    for (const r of graph.relationships) {
      if (!r.from || !r.to) continue;
      const fromId = `${r.fromType}:${r.from}`;
      const toId   = `${r.toType}:${r.to}`;
      const key    = `${fromId}||${r.rel}||${toId}`;
      if (nodeMap.has(fromId) && nodeMap.has(toId) && !seen.has(key)) {
        seen.add(key);
        relList.push({
          id:          key,
          from:        fromId,
          to:          toId,
          caption:     r.rel.replace(/_/g, " "),
          captionSize: 8,
          color:       "#018BFF",
          width:       1.5,
        });
      }
    }

    return { nodes: [...nodeMap.values()], rels: relList };
  }, [graph]);

  // Which labels actually appear in this graph (for the legend)
  const activeLabels = useMemo(() => {
    const seen = new Set(graph.entities.map((e) => e.label));
    return LEGEND.filter((l) => seen.has(l.label));
  }, [graph]);

  if (nodes.length === 0) return null;

  return (
    <div className="w-full flex flex-col gap-2">
      {/* Canvas */}
      <div
        style={{
          height: "300px",
          width: "100%",
          background: "#0A2540",
          borderRadius: "0.5rem",
          border: "1px solid #1e3a5f",
          position: "relative",
        }}
      >
        <InteractiveNvlWrapper
          ref={nvlRef}
          nodes={nodes}
          rels={rels}
          layout="forceDirected"
          nvlOptions={{
            disableTelemetry: true,
            initialZoom: 0.5,
            styling: {
              defaultNodeColor:         "#6b7280",
              defaultRelationshipColor: "#018BFF",
              nodeDefaultBorderColor:   "#1e3a5f",
              dropShadowColor:          "#018BFF",
            },
          }}
          nvlCallbacks={{
            onLayoutDone: () => nvlRef.current?.fit?.({ animated: false }),
          }}
          mouseEventCallbacks={{
            onHover: () => undefined,
            onNodeClick: () => undefined,
          }}
          style={{ width: "100%", height: "100%" }}
        />
      </div>

      {/* Legend */}
      {activeLabels.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {activeLabels.map(({ label, color }) => (
            <span key={label} className="flex items-center gap-1 text-xs text-gray-400">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full flex-none"
                style={{ background: color }}
              />
              {label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
