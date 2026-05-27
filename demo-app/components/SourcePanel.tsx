"use client";

import { SourceChunk } from "@/lib/search";
import { AnchorParam, ToolCallResult, ToolName } from "@/lib/agent";

interface Props {
  chunks: SourceChunk[];
  toolCalls: ToolCallResult[];
  agent: boolean;
  loading: boolean;
  /** Render at natural height (no h-full / overflow-y-auto) for inline use inside a scrolling parent */
  inline?: boolean;
}

// ── Entity label colours ──────────────────────────────────────────────────────

const LABEL_COLORS: Record<string, string> = {
  Person:        "bg-blue-900 text-blue-200",
  Place:         "bg-amber-900 text-amber-200",
  WaterBody:     "bg-cyan-900 text-cyan-200",
  AnimalSpecies: "bg-green-900 text-green-200",
  PlantSpecies:  "bg-lime-900 text-lime-200",
  NativeNation:  "bg-purple-900 text-purple-200",
};

function labelBadge(label: string, name: string) {
  const cls = LABEL_COLORS[label] ?? "bg-gray-700 text-gray-200";
  return (
    <span key={`${label}:${name}`} className={`inline-block text-xs px-1.5 py-0.5 rounded ${cls} mr-1 mb-1`}>
      {name}
    </span>
  );
}

// ── Tool badge ────────────────────────────────────────────────────────────────

const TOOL_STYLE: Record<ToolName, { label: string; cls: string }> = {
  vector_search:    { label: "vector_search",    cls: "bg-blue-900/50 text-blue-300 border border-blue-700/50" },
  sequence_context: { label: "sequence_context", cls: "bg-cyan-900/50 text-cyan-300 border border-cyan-700/50" },
  cypher_query:     { label: "cypher_query",     cls: "bg-green-900/50 text-green-300 border border-green-700/50" },
};

// ── Cypher result table ───────────────────────────────────────────────────────

function CypherResults({ results }: { results: Record<string, unknown>[] }) {
  if (results.length === 0) return <p className="text-xs text-gray-500 mt-1">No results returned.</p>;
  const keys = Object.keys(results[0]);
  const shown = results.slice(0, 12);
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="text-xs w-full border-collapse">
        <thead>
          <tr>
            {keys.map((k) => (
              <th key={k} className="text-left text-gray-500 font-mono pr-3 pb-1 border-b border-gray-700">
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              {keys.map((k) => (
                <td key={k} className="text-gray-300 pr-3 py-0.5 font-mono">
                  {String(row[k] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {results.length > 12 && (
        <p className="text-xs text-gray-600 mt-1">… and {results.length - 12} more rows</p>
      )}
    </div>
  );
}

// ── Resolved parameter list ───────────────────────────────────────────────────

const ENTITY_LABEL_COLOR: Record<string, string> = {
  Person:        "text-blue-300",
  Place:         "text-amber-300",
  WaterBody:     "text-cyan-300",
  AnimalSpecies: "text-green-300",
  PlantSpecies:  "text-lime-300",
  NativeNation:  "text-purple-300",
};

function AnchorParamList({ params }: { params: AnchorParam[] }) {
  return (
    <div className="mt-1 mb-2 rounded border border-gray-700/60 bg-gray-900/40 px-2.5 py-1.5 text-xs space-y-1">
      <p className="text-gray-500 uppercase tracking-wider text-[10px] font-semibold mb-1">
        Resolved parameters
      </p>
      {params.map((p) => {
        const isDate = p.source === "vector_search";
        const valueColor = isDate ? "text-amber-200" : (ENTITY_LABEL_COLOR[p.entityLabel ?? ""] ?? "text-gray-200");
        return (
          <div key={p.paramName} className="space-y-0.5">
            <div className="flex items-baseline gap-1.5 flex-wrap">
              <span className="font-mono text-neo-green/80">${p.paramName}</span>
              <span className="text-gray-600">=</span>
              <span className={`font-mono ${valueColor}`}>{p.value}</span>
              <span className="text-gray-600 text-[10px]">
                {isDate
                  ? `— corpus date, vector search${p.score !== undefined ? ` (score ${p.score.toFixed(3)})` : ""}`
                  : p.resolvedVia === "vector"
                    ? `— ${p.entityLabel ?? "entity"}, via vector index${p.score !== undefined ? ` (score ${p.score.toFixed(3)})` : ""}`
                    : `— ${p.entityLabel ?? "entity"}, via full-text index`}
              </span>
            </div>
            {p.description && (
              <p className="text-[10px] text-gray-600 italic pl-4 leading-snug">
                {p.description}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Tool call card ────────────────────────────────────────────────────────────

function ToolCallCard({ result, step, isLast }: { result: ToolCallResult; step: number; isLast: boolean }) {
  const style = TOOL_STYLE[result.tool];
  return (
    <div className="flex gap-3">

      {/* Step spine */}
      <div className="flex flex-col items-center flex-none">
        <div className="w-6 h-6 rounded-full border border-neo-green/60 bg-neo-green/10
                        flex items-center justify-center text-[10px] font-bold text-neo-green flex-none">
          {step}
        </div>
        {!isLast && <div className="w-px flex-1 bg-neo-green/20 mt-1" />}
      </div>

      {/* Card */}
      <div className={`flex-1 bg-neo-panel border border-neo-border rounded-lg p-3 ${!isLast ? "mb-3" : ""}`}>
        <div className="flex items-center gap-2 mb-2">
          <span className={`text-xs font-mono px-2 py-0.5 rounded ${style.cls}`}>
            {style.label}
          </span>
          {result.error && <span className="text-xs text-red-400">error</span>}
        </div>

        {/* Input */}
        <p className="text-xs text-gray-500 italic mb-1">"{result.input}"</p>

        {/* Resolved Cypher parameters — shows how entity names and dates were grounded */}
        {result.anchorParams && result.anchorParams.length > 0 && (
          <AnchorParamList params={result.anchorParams} />
        )}

        {/* Generated Cypher */}
        {result.cypher && (
          <pre className="text-xs font-mono bg-[#0A2540] text-green-300 rounded p-2 mt-2 overflow-x-auto whitespace-pre-wrap">
            {result.cypher}
          </pre>
        )}

        {/* Cypher results table */}
        {result.cypherResults && <CypherResults results={result.cypherResults} />}

        {/* Passage count for vector/sequence tools */}
        {result.chunks && (
          <p className="text-xs text-gray-600 mt-1">
            {result.chunks.filter(c => c.retrieval !== "sequence").length} passage(s) retrieved
            {result.chunks.some(c => c.retrieval === "sequence")
              ? `, ${result.chunks.filter(c => c.retrieval === "sequence").length} via NEXT_CHUNK`
              : ""}
          </p>
        )}

        {result.error && <p className="text-xs text-red-400 mt-1">{result.error}</p>}
      </div>
    </div>
  );
}


// ── Main component ────────────────────────────────────────────────────────────

export default function SourcePanel({ chunks, toolCalls, agent, loading, inline }: Props) {
  const scrollCls = inline ? "flex flex-col gap-3 p-4" : "flex flex-col gap-3 h-full overflow-y-auto p-4";

  if (loading) {
    return (
      <aside className={scrollCls}>
        {[1, 2, 3].map((i) => (
          <div key={i} className="bg-neo-panel border border-neo-border rounded-lg p-3 animate-pulse">
            <div className="h-3 bg-gray-700 rounded w-1/3 mb-2" />
            <div className="h-2 bg-gray-700 rounded w-full mb-1" />
            <div className="h-2 bg-gray-700 rounded w-5/6" />
          </div>
        ))}
      </aside>
    );
  }

  if (!chunks.length && !toolCalls.length) {
    return (
      <aside className={inline ? "p-4 text-gray-600 text-sm" : "flex items-center justify-center h-full text-gray-600 text-sm p-4 text-center"}>
        Retrieved passages and graph context will appear here after you ask a question.
      </aside>
    );
  }

  return (
    <aside className={`${inline ? "flex flex-col gap-4 p-4" : "flex flex-col gap-4 h-full overflow-y-auto p-4"}`}>

      {/* Tool pipeline — agent mode */}
      {agent && toolCalls.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-neo-green mb-3">
            Retrieval pipeline
          </h3>
          <div>
            {toolCalls.map((r, i) => (
              <ToolCallCard
                key={r.callId}
                result={r}
                step={i + 1}
                isLast={i === toolCalls.length - 1}
              />
            ))}
          </div>
        </section>
      )}

      {/* Passages — only show if any non-cypher tool was called, or vector mode */}
      {chunks.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
            Journal passages ({chunks.length})
            {agent && chunks.some(c => c.retrieval === "sequence") && (
              <span className="ml-2 text-neo-green normal-case font-normal">
                · includes NEXT_CHUNK context
              </span>
            )}
          </h3>
          <div className="flex flex-col gap-3">
            {chunks.map((chunk) => {
              const isSeq = chunk.retrieval === "sequence";
              return (
                <div
                  key={chunk.chunkId}
                  className={`rounded-lg p-3 border ${
                    isSeq
                      ? "bg-[#0a1a10] border-neo-green/40"
                      : "bg-neo-panel border-neo-border"
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-xs text-gray-500">
                      {[chunk.author, chunk.date].filter(Boolean).join(" · ")}
                    </div>
                    {isSeq ? (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-neo-green/10 text-neo-green font-mono">
                        NEXT_CHUNK
                      </span>
                    ) : chunk.score > 0 ? (
                      <div
                        className="text-xs font-mono px-1.5 py-0.5 rounded"
                        style={{
                          backgroundColor: `rgba(1,139,255,${chunk.score * 0.4})`,
                          color: "#018BFF",
                        }}
                      >
                        {chunk.score.toFixed(3)}
                      </div>
                    ) : null}
                  </div>
                  <p className="text-xs text-gray-300 leading-relaxed line-clamp-4">{chunk.text}</p>
                  {agent && chunk.entities?.length > 0 && (
                    <div className="mt-2 flex flex-wrap">
                      {chunk.entities.map((e) => labelBadge(e.label, e.name))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </aside>
  );
}
