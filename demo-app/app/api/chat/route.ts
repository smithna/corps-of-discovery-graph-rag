import { NextRequest } from "next/server";
import {
  embedText,
  vectorSearch,
  buildVectorContext,
  Chunk,
} from "@/lib/search";
import { runAgent, AgentEvent } from "@/lib/agent";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const { message, mode = "vector", history = [] } = await req.json();
  if (!message?.trim()) {
    return Response.json({ error: "No message provided" }, { status: 400 });
  }

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      const send = (obj: unknown) =>
        controller.enqueue(encoder.encode(JSON.stringify(obj) + "\n"));

      try {
        // Embed once — used by both modes
        const embedding = await embedText(message);

        if (mode === "agent") {
          // ── Graph Agent mode: tool-calling loop ──────────────────────────────
          for await (const event of runAgent(message, embedding, history)) {
            send(event);
          }
        } else {
          // ── Vector-only mode ─────────────────────────────────────────────────
          const chunks: Chunk[] = await vectorSearch(embedding, 8);
          const context = buildVectorContext(chunks);

          send({
            type: "sources",
            chunks: chunks.map((c) => ({
              ...c,
              score: Math.round(c.score * 1000) / 1000,
              entities: [],
            })),
            graph: { entities: [], relationships: [] },
            toolCalls: [],
          });

          const { streamAnswer } = await import("@/lib/search");
          for await (const token of streamAnswer(message, context, false, history)) {
            send({ type: "token", content: token });
          }
          send({ type: "done" });
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        send({ type: "error", message: msg });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson",
      "Cache-Control": "no-cache",
    },
  });
}
