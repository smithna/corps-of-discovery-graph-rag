import OpenAI from "openai";
import { runQuery } from "./neo4j";

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

// Neo4j returns date properties as driver Date objects, not strings.
// This helper converts either form to an ISO string (or null).
const toDateStr = (d: unknown): string | null =>
  d == null ? null : typeof d === "string" ? d : String(d);

export interface Chunk {
  chunkId: string;
  text: string;
  date: string | null;
  author: string | null;
  score: number;
}

export interface GraphEntity {
  label: string;
  name: string;
}

export interface GraphRelationship {
  fromType: string;
  from: string;
  rel: string;
  toType: string;
  to: string;
}

export interface SourceChunk extends Chunk {
  entities: GraphEntity[];
  /** How this chunk was retrieved in graph mode */
  retrieval?: "vector" | "sequence";
}

export interface GraphContext {
  entities: GraphEntity[];
  relationships: GraphRelationship[];
}

// ── Embed the user query ───────────────────────────────────────────────────────

export async function embedText(text: string): Promise<number[]> {
  const resp = await openai.embeddings.create({
    model: process.env.OPENAI_EMBEDDING_MODEL ?? "text-embedding-3-small",
    input: text,
  });
  return resp.data[0].embedding;
}

// ── Vector-only search ────────────────────────────────────────────────────────

export async function vectorSearch(embedding: number[], k = 8): Promise<Chunk[]> {
  const rows = await runQuery<{
    chunkId: string;
    text: string;
    date: string | null;
    author: string | null;
    score: number;
  }>(
    `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $embedding)
     YIELD node AS chunk, score
     RETURN chunk.chunkId AS chunkId,
            chunk.text     AS text,
            chunk.date     AS date,
            chunk.author   AS author,
            score
     ORDER BY score DESC`,
    { k, embedding }
  );
  return rows.map((r) => ({ ...r, date: toDateStr(r.date) }));
}

// ── Vector search + graph enrichment ─────────────────────────────────────────
//
// Graph mode does two things beyond vector search:
//   1. NEXT_CHUNK expansion — for the top vector results, fetch the preceding
//      journal entries (up to 4 hops back) so questions like "what happened
//      before X" can be answered from the corpus rather than synthesised.
//   2. Entity + relationship context — finds named entities (people, places,
//      species) mentioned in all retrieved chunks and the relationships between
//      them, giving the LLM structured facts alongside the raw text.

export async function vectorGraphSearch(
  embedding: number[],
  k = 8
): Promise<{ chunks: SourceChunk[]; graph: GraphContext }> {

  // ── Step 1: Vector search ─────────────────────────────────────────────────
  const vectorRows = await runQuery<{
    chunkId: string; text: string; date: string | null;
    author: string | null; score: number;
  }>(
    `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $embedding)
     YIELD node AS chunk, score
     RETURN chunk.chunkId AS chunkId, chunk.text AS text,
            chunk.date AS date, chunk.author AS author, score
     ORDER BY score DESC`,
    { k, embedding }
  );

  const vectorChunkIds = vectorRows.map((r) => r.chunkId);

  // ── Step 2: NEXT_CHUNK expansion ──────────────────────────────────────────
  // For the top-4 vector results, walk backward up to 4 hops to surface
  // preceding journal entries. This is what answers "what happened before X".
  // NEXT_CHUNK goes (earlier)-[:NEXT_CHUNK]->(later), so preceding chunks
  // satisfy: (preceding)-[:NEXT_CHUNK*1..4]->(anchor).
  const anchorIds = vectorChunkIds.slice(0, 4);
  const sequenceRows = await runQuery<{
    chunkId: string; text: string; date: string | null; author: string | null;
  }>(
    `UNWIND $anchorIds AS anchorId
     MATCH (preceding:Chunk)-[:NEXT_CHUNK*1..4]->(anchor:Chunk {chunkId: anchorId})
     WHERE NOT preceding.chunkId IN $vectorChunkIds
     RETURN DISTINCT
            preceding.chunkId AS chunkId, preceding.text AS text,
            preceding.date    AS date,    preceding.author AS author
     ORDER BY preceding.date ASC
     LIMIT 8`,
    { anchorIds, vectorChunkIds }
  );

  // ── Step 3: Combine all chunk IDs and fetch entities ─────────────────────
  const allChunkIds = [
    ...vectorChunkIds,
    ...sequenceRows.map((r) => r.chunkId),
  ];

  const entityRows = await runQuery<{
    chunkId: string;
    entities: GraphEntity[];
  }>(
    `UNWIND $chunkIds AS cid
     MATCH (c:Chunk {chunkId: cid})
     OPTIONAL MATCH (entity)-[:MENTIONED_IN]->(c)
     WHERE NOT entity:GenericLocation AND NOT entity:Chunk
     WITH c.chunkId AS chunkId,
          collect(DISTINCT {
            label: head(labels(entity)),
            name:  coalesce(entity.canonicalName, entity.name, '')
          }) AS entities
     RETURN chunkId, entities`,
    { chunkIds: allChunkIds }
  );

  const entityByChunk = new Map(entityRows.map((r) => [r.chunkId, r.entities ?? []]));

  // ── Step 4: Relationships across all retrieved chunks ─────────────────────
  const relRows = await runQuery<GraphRelationship>(
    `UNWIND $chunkIds AS cid
     MATCH (a)-[:MENTIONED_IN]->(c:Chunk {chunkId: cid})
     WHERE NOT a:GenericLocation AND NOT a:Chunk
     MATCH (a)-[r]->(b)
     WHERE type(r) <> 'MENTIONED_IN'
       AND NOT b:Chunk AND NOT b:GenericLocation
       AND EXISTS {
         MATCH (b)-[:MENTIONED_IN]->(c2:Chunk)
         WHERE c2.chunkId IN $chunkIds
       }
     RETURN DISTINCT
            head(labels(a))                       AS fromType,
            coalesce(a.canonicalName, a.name, '') AS from,
            type(r)                               AS rel,
            head(labels(b))                       AS toType,
            coalesce(b.canonicalName, b.name, '') AS to
     LIMIT 40`,
    { chunkIds: allChunkIds }
  );

  // ── Assemble result ───────────────────────────────────────────────────────
  const vectorChunks: SourceChunk[] = vectorRows.map((r) => ({
    ...r,
    date: toDateStr(r.date),
    retrieval: "vector" as const,
    entities: entityByChunk.get(r.chunkId) ?? [],
  }));

  const sequenceChunks: SourceChunk[] = sequenceRows.map((r) => ({
    ...r,
    date: toDateStr(r.date),
    score: 0,
    retrieval: "sequence" as const,
    entities: entityByChunk.get(r.chunkId) ?? [],
  }));

  const entityMap = new Map<string, GraphEntity>();
  for (const chunks of [vectorChunks, sequenceChunks]) {
    for (const c of chunks) {
      for (const e of c.entities) {
        if (e.name) entityMap.set(`${e.label}:${e.name}`, e);
      }
    }
  }

  return {
    chunks: [...vectorChunks, ...sequenceChunks],
    graph: {
      entities: [...entityMap.values()],
      relationships: relRows,
    },
  };
}

// ── Build LLM context strings ─────────────────────────────────────────────────

export function buildVectorContext(chunks: Chunk[]): string {
  return chunks
    .map((c, i) => {
      const header = [c.author, c.date].filter(Boolean).join(" · ");
      return `[Passage ${i + 1}${header ? ` — ${header}` : ""}]\n${c.text}`;
    })
    .join("\n\n---\n\n");
}

export function buildGraphContext(
  chunks: SourceChunk[],
  graph: GraphContext
): string {
  const vectorChunks   = chunks.filter((c) => c.retrieval !== "sequence");
  const sequenceChunks = chunks.filter((c) => c.retrieval === "sequence");

  function formatChunk(c: SourceChunk, i: number, label: string) {
    const header = [c.author, c.date].filter(Boolean).join(" · ");
    const entityList =
      c.entities?.length
        ? `  Entities: ${c.entities.map((e) => `${e.name} (${e.label})`).join(", ")}`
        : "";
    return `[${label} ${i + 1}${header ? ` — ${header}` : ""}]\n${c.text}${entityList ? "\n" + entityList : ""}`;
  }

  const vectorSection = vectorChunks.length > 0
    ? "=== PASSAGES (retrieved by vector similarity) ===\n\n" +
      vectorChunks.map((c, i) => formatChunk(c, i + 1, "Passage")).join("\n\n---\n\n")
    : "";

  // Sort sequence chunks by date ascending so they read chronologically
  const sortedSeq = [...sequenceChunks].sort((a, b) =>
    (toDateStr(a.date) ?? "").localeCompare(toDateStr(b.date) ?? "")
  );
  const sequenceSection = sortedSeq.length > 0
    ? "\n\n=== PRECEDING JOURNAL ENTRIES (retrieved by NEXT_CHUNK graph traversal — chronological order) ===\n\n" +
      sortedSeq.map((c, i) => formatChunk(c, i + 1, "Entry")).join("\n\n---\n\n")
    : "";

  const rels =
    graph.relationships.length > 0
      ? "\n\n=== GRAPH RELATIONSHIPS ===\n" +
        graph.relationships
          .map((r) => `${r.from} (${r.fromType}) -[${r.rel}]-> ${r.to} (${r.toType})`)
          .join("\n")
      : "";

  return vectorSection + sequenceSection + rels;
}

// ── Stream LLM answer ─────────────────────────────────────────────────────────

const CONTEXT_ONLY = `Answer using ONLY the information in the provided context. \
Do not add facts, dates, names, or events from your training knowledge. \
If the context does not contain enough information to answer the question fully, say so explicitly.`;

const SYSTEM_VECTOR = `You are an assistant that answers questions about the Lewis & Clark Expedition \
using retrieved journal passages. ${CONTEXT_ONLY}`;

const SYSTEM_GRAPH = `You are an assistant that answers questions about the Lewis & Clark Expedition \
using retrieved context, which may include three sections:

1. PASSAGES — journal entries retrieved by semantic similarity.
2. PRECEDING JOURNAL ENTRIES — entries retrieved by graph traversal (NEXT_CHUNK relationships), \
   in chronological order. Use these for sequence or timeline questions.
3. GRAPH RELATIONSHIPS — named entities and relationships from the retrieved entries.

${CONTEXT_ONLY}`;

export async function* streamAnswer(
  question: string,
  context: string,
  useGraph: boolean,
  history: { role: "user" | "assistant"; content: string }[]
): AsyncGenerator<string> {
  const systemPrompt = useGraph ? SYSTEM_GRAPH : SYSTEM_VECTOR;
  const userContent = `Context:\n\n${context}\n\n---\n\nQuestion: ${question}`;

  const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: systemPrompt },
    ...history.slice(-6), // keep last 3 turns for follow-up questions
    { role: "user", content: userContent },
  ];

  const stream = await openai.chat.completions.create({
    model: process.env.OPENAI_CHAT_MODEL ?? "gpt-4o",
    temperature: 0.2,
    messages,
    stream: true,
  });

  for await (const chunk of stream) {
    const token = chunk.choices[0]?.delta?.content;
    if (token) yield token;
  }
}
