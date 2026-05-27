"use client";

import { useState, useRef, useCallback } from "react";
import SourcePanel from "@/components/SourcePanel";
import { SourceChunk } from "@/lib/search";
import { ToolCallResult } from "@/lib/agent";

const EXAMPLE_QUESTIONS = [
  "What did Lewis write about grizzly bears?",
  "What species did Clark observe near the Yellowstone River?",
  "What places did the corps visit in the month before the birth of Charbonneau and Sacagawea's son?",
  "What are the different names for Toussaint Charbonneau in the journals?",
  "What mammal species did the expedition observe between the time that they reached the Platte River and the time they reached the Rocky Mountains?",
  "Which Native Nations did Lewis personally meet with?",
  "In the month after Sacagawea is first mentioned in the journals, what native nations did the expedition encounter?",
];

// ── NDJSON stream reader ──────────────────────────────────────────────────────

async function* readNDJSON(resp: Response) {
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) yield JSON.parse(line);
    }
  }
}

// ── Answer display ────────────────────────────────────────────────────────────

function AnswerPane({
  text,
  streaming,
  loading,
}: {
  text: string;
  streaming: string;
  loading: boolean;
}) {
  const content = streaming || text;

  if (!content && loading) {
    return (
      <div className="flex gap-1 items-center h-6 px-1">
        {[0, 150, 300].map((delay) => (
          <div
            key={delay}
            className="w-1.5 h-1.5 bg-gray-600 rounded-full animate-bounce"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    );
  }

  if (!content) return null;

  return (
    <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
      {content}
      {streaming && (
        <span className="inline-block w-0.5 h-4 bg-neo-blue ml-0.5 animate-pulse align-middle" />
      )}
    </p>
  );
}

// ── Column header ─────────────────────────────────────────────────────────────

function ColumnLabel({
  label,
  dotColor,
  loading,
}: {
  label: string;
  dotColor: string;
  loading: boolean;
}) {
  return (
    <div className="flex-none flex items-center gap-2 px-4 py-2.5 border-b border-neo-border bg-neo-panel/50">
      <div className={`w-2 h-2 rounded-full ${dotColor} ${loading ? "animate-pulse" : ""}`} />
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{label}</span>
      {loading && <span className="text-xs text-gray-600 ml-auto">Retrieving…</span>}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Home() {
  const [input, setInput]             = useState("");
  const [question, setQuestion]       = useState("");

  // Vector column
  const [vectorAnswer, setVectorAnswer]       = useState("");
  const [vectorStreaming, setVectorStreaming]  = useState("");
  const [vectorLoading, setVectorLoading]     = useState(false);
  const [vectorChunks, setVectorChunks]       = useState<SourceChunk[]>([]);

  // Agent column
  const [agentAnswer, setAgentAnswer]         = useState("");
  const [agentStreaming, setAgentStreaming]    = useState("");
  const [agentLoading, setAgentLoading]       = useState(false);
  const [agentChunks, setAgentChunks]         = useState<SourceChunk[]>([]);
  const [agentToolCalls, setAgentToolCalls]   = useState<ToolCallResult[]>([]);

  const vectorAbortRef = useRef<AbortController | null>(null);
  const agentAbortRef  = useRef<AbortController | null>(null);

  const loading = vectorLoading || agentLoading;

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || loading) return;
    const q = text.trim();

    // Reset everything for the new question
    setInput("");
    setQuestion(q);
    setVectorAnswer("");
    setVectorStreaming("");
    setVectorChunks([]);
    setAgentAnswer("");
    setAgentStreaming("");
    setAgentChunks([]);
    setAgentToolCalls([]);
    setVectorLoading(true);
    setAgentLoading(true);

    vectorAbortRef.current?.abort();
    agentAbortRef.current?.abort();
    const vAbort = new AbortController();
    const aAbort = new AbortController();
    vectorAbortRef.current = vAbort;
    agentAbortRef.current  = aAbort;

    // ── Vector stream ────────────────────────────────────────────────────
    (async () => {
      try {
        const resp = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: q, mode: "vector", history: [] }),
          signal: vAbort.signal,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        let accumulated = "";
        for await (const evt of readNDJSON(resp)) {
          if (evt.type === "sources") {
            setVectorChunks(evt.chunks);
            setVectorLoading(false);
          } else if (evt.type === "token") {
            accumulated += evt.content;
            setVectorStreaming(accumulated);
          } else if (evt.type === "done") {
            setVectorAnswer(accumulated);
            setVectorStreaming("");
          } else if (evt.type === "error") {
            throw new Error(evt.message);
          }
        }
      } catch (err: unknown) {
        if ((err as Error).name !== "AbortError") {
          setVectorAnswer(`Error: ${(err as Error).message}`);
        }
      } finally {
        setVectorLoading(false);
        setVectorStreaming("");
      }
    })();

    // ── Agent stream ─────────────────────────────────────────────────────
    (async () => {
      try {
        const resp = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: q, mode: "agent", history: [] }),
          signal: aAbort.signal,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        let accumulated = "";
        const liveToolCalls: ToolCallResult[] = [];
        for await (const evt of readNDJSON(resp)) {
          if (evt.type === "tool_result") {
            liveToolCalls.push(evt.result);
            setAgentToolCalls([...liveToolCalls]);
          } else if (evt.type === "sources") {
            setAgentChunks(evt.chunks);
            if (evt.toolCalls?.length) setAgentToolCalls(evt.toolCalls);
            setAgentLoading(false);
          } else if (evt.type === "token") {
            accumulated += evt.content;
            setAgentStreaming(accumulated);
          } else if (evt.type === "done") {
            setAgentAnswer(accumulated);
            setAgentStreaming("");
          } else if (evt.type === "error") {
            throw new Error(evt.message);
          }
        }
      } catch (err: unknown) {
        if ((err as Error).name !== "AbortError") {
          setAgentAnswer(`Error: ${(err as Error).message}`);
        }
      } finally {
        setAgentLoading(false);
        setAgentStreaming("");
      }
    })();
  }, [loading]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-neo-dark text-white">

      {/* ── Header ── */}
      <header className="flex-none flex items-center px-6 py-3 border-b border-neo-border bg-neo-panel">
        <div className="flex items-center gap-3">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="14" cy="14" r="14" fill="#018BFF" fillOpacity="0.15"/>
            <circle cx="14" cy="14" r="5" fill="#018BFF"/>
            <circle cx="6"  cy="8"  r="3" fill="#018BFF" fillOpacity="0.7"/>
            <circle cx="22" cy="8"  r="3" fill="#018BFF" fillOpacity="0.7"/>
            <circle cx="6"  cy="20" r="3" fill="#018BFF" fillOpacity="0.7"/>
            <circle cx="22" cy="20" r="3" fill="#018BFF" fillOpacity="0.7"/>
            <line x1="14" y1="14" x2="6"  y2="8"  stroke="#018BFF" strokeOpacity="0.5" strokeWidth="1.5"/>
            <line x1="14" y1="14" x2="22" y2="8"  stroke="#018BFF" strokeOpacity="0.5" strokeWidth="1.5"/>
            <line x1="14" y1="14" x2="6"  y2="20" stroke="#018BFF" strokeOpacity="0.5" strokeWidth="1.5"/>
            <line x1="14" y1="14" x2="22" y2="20" stroke="#018BFF" strokeOpacity="0.5" strokeWidth="1.5"/>
          </svg>
          <div>
            <h1 className="text-sm font-semibold leading-none">Lewis &amp; Clark GraphRAG</h1>
            <p className="text-xs text-gray-500 leading-none mt-0.5">Vector RAG vs Graph RAG — side by side</p>
          </div>
        </div>
      </header>

      {/* ── Current question banner ── */}
      {question && (
        <div className="flex-none px-6 py-2.5 border-b border-neo-border bg-neo-panel/30">
          <p className="text-sm text-gray-300 font-medium">{question}</p>
        </div>
      )}

      {/* ── Two-column body ── */}
      <div className="flex flex-1 min-h-0">

        {/* Vector RAG column */}
        <div className="flex flex-col flex-1 min-w-0 border-r border-neo-border overflow-hidden">
          <ColumnLabel label="Vector RAG" dotColor="bg-neo-blue" loading={vectorLoading} />
          <div className="flex-1 overflow-y-auto">
            {/* Answer */}
            <div className="px-4 py-4 border-b border-neo-border/40">
              {!question ? (
                <p className="text-sm text-gray-600">Ask a question to see a comparison.</p>
              ) : (
                <AnswerPane
                  text={vectorAnswer}
                  streaming={vectorStreaming}
                  loading={vectorLoading}
                />
              )}
            </div>
            {/* Sources */}
            {(vectorChunks.length > 0 || (vectorLoading && !vectorAnswer && !vectorStreaming)) && (
              <>
                <div className="px-4 pt-3 pb-1">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                    Retrieved Sources
                  </h3>
                </div>
                <SourcePanel
                  chunks={vectorChunks}
                  toolCalls={[]}
                  agent={false}
                  loading={vectorLoading && vectorChunks.length === 0}
                  inline
                />
              </>
            )}
          </div>
        </div>

        {/* Graph RAG column */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <ColumnLabel label="Graph RAG" dotColor="bg-neo-green" loading={agentLoading} />
          <div className="flex-1 overflow-y-auto">
            {/* Answer */}
            <div className="px-4 py-4 border-b border-neo-border/40">
              {!question ? (
                <p className="text-sm text-gray-600">Graph RAG uses structured knowledge retrieval to ground its answers.</p>
              ) : (
                <AnswerPane
                  text={agentAnswer}
                  streaming={agentStreaming}
                  loading={agentLoading}
                />
              )}
            </div>
            {/* Sources */}
            {(agentToolCalls.length > 0 || agentChunks.length > 0 || (agentLoading && !agentAnswer && !agentStreaming)) && (
              <>
                <div className="px-4 pt-3 pb-1">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                    Agent Tools + Sources
                  </h3>
                </div>
                <SourcePanel
                  chunks={agentChunks}
                  toolCalls={agentToolCalls}
                  agent={true}
                  loading={agentLoading && agentToolCalls.length === 0 && agentChunks.length === 0}
                  inline
                />
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Input bar (always visible) ── */}
      <div className="flex-none border-t border-neo-border bg-neo-panel px-4 pb-4 pt-3">
        <div className="flex flex-wrap gap-2 mb-3">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => sendMessage(q)}
              disabled={loading}
              className="text-xs px-3 py-1.5 rounded-full border border-neo-border text-gray-400 hover:border-neo-blue hover:text-neo-blue transition-colors disabled:opacity-40"
            >
              {q}
            </button>
          ))}
        </div>
        <div className="flex gap-2 items-end bg-neo-dark border border-neo-border rounded-xl p-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about the expedition… (Enter to send)"
            rows={1}
            className="flex-1 bg-transparent text-sm text-white placeholder-gray-600 resize-none outline-none leading-relaxed max-h-32 overflow-y-auto"
            style={{ fieldSizing: "content" } as React.CSSProperties}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || loading}
            className="flex-none w-8 h-8 rounded-lg bg-neo-blue disabled:opacity-30 hover:bg-blue-500 transition-colors flex items-center justify-center"
          >
            {loading ? (
              <svg className="animate-spin w-4 h-4 text-white" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
            ) : (
              <svg className="w-4 h-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
