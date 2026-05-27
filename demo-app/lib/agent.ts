import OpenAI from "openai";
import { runQuery } from "./neo4j";

const toDateStr = (d: unknown): string | null =>
  d == null ? null : typeof d === "string" ? d : String(d);
import {
  embedText,
  vectorSearch,
  GraphContext,
  GraphEntity,
  GraphRelationship,
  SourceChunk,
} from "./search";
import { GRAPH_SCHEMA, FEW_SHOT } from "./cypher-config";

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

// ── Types ─────────────────────────────────────────────────────────────────────

export type ToolName = "vector_search" | "sequence_context" | "cypher_query";

/**
 * A single Cypher parameter resolved before query generation.
 * Entity params come from a graph lookup against words in the question;
 * the date anchor comes from a vector search against the corpus.
 */
export interface AnchorParam {
  /** Parameter name used in the Cypher, e.g. "person", "waterBody", "anchorDate" */
  paramName: string;
  /** Resolved canonical value, e.g. "MERIWETHER LEWIS", "1805-02-11" */
  value: string;
  /** How this value was found */
  source: "graph_lookup" | "vector_search";
  /** Whether the entity was resolved via full-text or vector index (graph_lookup only) */
  resolvedVia?: "fulltext" | "vector";
  /** Node label (graph_lookup only), e.g. "Person", "WaterBody" */
  entityLabel?: string;
  /** The text that was embedded to produce the entity's vector */
  description?: string;
  /** The capitalised word from the question that triggered a full-text match */
  matchedTerm?: string;
  /** Cosine similarity score (vector matches) */
  score?: number;
}

export interface ToolCallResult {
  callId: string;
  tool: ToolName;
  input: string;
  /** Generated Cypher (cypher_query only) */
  cypher?: string;
  /** Parameters resolved before Cypher generation (cypher_query only) */
  anchorParams?: AnchorParam[];
  /** Raw query results (cypher_query only) */
  cypherResults?: Record<string, unknown>[];
  /** Passage chunks (vector_search / sequence_context) */
  chunks?: SourceChunk[];
  /** Entity + relationship context (sequence_context) */
  graph?: GraphContext;
  error?: string;
}

export type AgentEvent =
  | { type: "tool_start"; callId: string; tool: ToolName; input: string }
  | { type: "tool_result"; result: ToolCallResult }
  | { type: "sources"; chunks: SourceChunk[]; graph: GraphContext; toolCalls: ToolCallResult[] }
  | { type: "token"; content: string }
  | { type: "done" }
  | { type: "error"; message: string };

// ── Cypher-first parameter resolution ────────────────────────────────────────
//
// Instead of guessing which entities to resolve from the question text, we ask
// the LLM to generate the Cypher first and declare exactly which parameters it
// needs and what label each one belongs to.  We then search only the per-label
// vector index for each declared parameter — no cross-label contamination.

/** One vector index per node label, created by embed_entities.py */
const LABEL_INDEX: Record<string, string> = {
  Person:        "person_embeddings",
  Place:         "place_embeddings",
  WaterBody:     "waterbody_embeddings",
  AnimalSpecies: "animalspecies_embeddings",
  PlantSpecies:  "plantspecies_embeddings",
  NativeNation:  "nativenation_embeddings",
  Event:         "event_embeddings",
  Taxon:         "taxon_embeddings",
};

/** A parameter slot declared by the LLM alongside the Cypher it generated. */
interface ParamDescriptor {
  /** Name used in the Cypher (without $), e.g. "taxon", "person", "anchorDate" */
  name: string;
  /**
   * Exact node label, or "date" to request an $anchorDate from corpus vector search.
   * Must match a key in LABEL_INDEX or be "date".
   */
  label: string;
  /** Natural-language description of what to find, e.g. "grasses, Poaceae family" */
  query: string;
}

interface CypherPlan {
  cypher: string;
  params: ParamDescriptor[];
}

/**
 * Ask the LLM to produce both the Cypher query and a typed descriptor for
 * every parameter it uses.  We use structured JSON output so the response is
 * always parseable.
 */
async function generateCypherPlan(question: string): Promise<CypherPlan> {
  const exampleJson = JSON.stringify(
    {
      cypher: [
        "MATCH (p:Person {canonicalName: $person})-[:OBSERVED]->(s)",
        "WHERE (s:AnimalSpecies OR s:PlantSpecies)",
        "MATCH (s)-[:MENTIONED_IN]->(c:Chunk)",
        "MATCH (w:WaterBody {canonicalName: $waterBody})-[:MENTIONED_IN]->(c)",
        "RETURN DISTINCT s.canonicalName AS species, head(labels(s)) AS type",
        "ORDER BY species LIMIT 30",
      ].join("\n"),
      params: [
        { name: "person",    label: "Person",    query: "Meriwether Lewis" },
        { name: "waterBody", label: "WaterBody", query: "Columbia River"   },
      ],
    },
    null, 2
  );

  const resp = await openai.chat.completions.create({
    model: process.env.OPENAI_CHAT_MODEL ?? "gpt-4o",
    temperature: 0,
    response_format: { type: "json_object" },
    messages: [
      {
        role: "system",
        content: [
          "You generate Cypher queries for a Neo4j knowledge graph.",
          "",
          "Output ONLY valid JSON in this exact format:",
          '{',
          '  "cypher": "MATCH ...",',
          '  "params": [',
          '    { "name": "paramName", "label": "NodeLabel", "query": "what to look up" }',
          '  ]',
          '}',
          "",
          "PARAMETER RULES:",
          "- Declare a param for every entity or date value the Cypher filters on",
          '- "label" must be exactly one of: Person, Place, WaterBody, AnimalSpecies,',
          "  PlantSpecies, NativeNation, Event, Taxon, date",
          '- Use label "date" for a $anchorDate anchored to the corpus via chunk vector search',
          '- "query" is the natural-language phrase used to find the right node in the graph',
          "  (e.g. \"Meriwether Lewis\", \"Columbia River\", \"Poaceae grass family\",",
          "  \"death of Sergeant Floyd\")",
          "- Taxon nodes use .name; all others use .canonicalName — match your Cypher",
          "- For Taxon params, include the desired rank in the query string so the right",
          "  taxonomic level is resolved — e.g. \"Salmonidae, family of salmon\" not \"salmon\",",
          "  \"Rodentia, order of rodents\" not \"rodent\", \"Aves, class of birds\" not \"birds\"",
          "- Only declare params you actually use in the Cypher",
          "- If the Cypher needs no params, set params to []",
          "",
          "CYPHER RULES:",
          "- NEVER hardcode names, species, places, or dates as literals — use $paramName",
          "- Always LIMIT 30 unless the question asks for a count or complete list",
          "- Filter generic locations: WHERE NOT node:GenericLocation",
          "- Use coalesce(x.canonicalName, x.name, '') for display names",
          "",
          "EXAMPLE:",
          `Q: "What species did Lewis observe near the Columbia River?"`,
          exampleJson,
          "",
          "SCHEMA:",
          GRAPH_SCHEMA,
          "",
          "ADDITIONAL CYPHER PATTERNS (apply the same JSON wrapping):",
          FEW_SHOT,
        ].join("\n"),
      },
      { role: "user", content: question },
    ],
  });

  const raw = resp.choices[0].message.content ?? "{}";
  try {
    const parsed = JSON.parse(raw);
    return {
      cypher: (parsed.cypher ?? "").trim(),
      params: Array.isArray(parsed.params) ? parsed.params : [],
    };
  } catch {
    // Last-resort fallback: treat the whole response as plain Cypher
    return { cypher: raw.trim(), params: [] };
  }
}

/**
 * Resolve a single declared parameter to a concrete value.
 * - label "date"          → vector search on Chunk nodes for an anchor date
 * - any entity label      → embed the query string, search the per-label index
 */
async function resolveParam(
  desc: ParamDescriptor,
  questionEmbedding: number[],
): Promise<AnchorParam | null> {
  // ── Date anchor ─────────────────────────────────────────────────────────────
  if (desc.label === "date") {
    const chunks = await vectorSearch(questionEmbedding, 3);
    const top = chunks.find((c) => c.date);
    if (!top) return null;
    return {
      paramName: desc.name,
      value:     top.date!,
      source:    "vector_search",
      score:     top.score,
    };
  }

  // ── Entity anchor ──────────────────────────────────────────────────────────
  //
  // Proper-noun labels (Person, Place, WaterBody, NativeNation) → per-label
  //   full-text index.  Token matching is more reliable than vector similarity
  //   for names like "Meriwether Lewis" or "Columbia River".
  //   Place and WaterBody share location_search so geographic queries work even
  //   when the LLM picks the wrong label for a river vs. a fort.
  //
  // Semantic labels (AnimalSpecies, PlantSpecies, Taxon, Event) → per-label
  //   vector index.  These queries are concepts ("salmon family", "birth of a
  //   child") that need semantic matching, not exact token hits.

  const LABEL_FULLTEXT_INDEX: Record<string, string> = {
    Person:       "person_search",
    Place:        "location_search",   // covers Place + WaterBody
    WaterBody:    "location_search",
    NativeNation: "native_nation_search",
  };

  const ftIndexName = LABEL_FULLTEXT_INDEX[desc.label];

  if (ftIndexName) {
    // Escape Lucene special characters so names like "O'Fallon" don't break the query
    const ftQuery = desc.query.replace(/[+\-&|!(){}\[\]^"~*?:\\/]/g, "\\$&");
    const rows = await runQuery<{ name: string; description: string; score: number }>(
      `CALL db.index.fulltext.queryNodes('${ftIndexName}', $query)
       YIELD node, score
       WHERE NOT node:GenericLocation
       RETURN coalesce(node.canonicalName, node.name) AS name,
              coalesce(node.embeddingDescription, '') AS description,
              score
       LIMIT 1`,
      { query: ftQuery }
    ).catch(() => []);

    if (rows.length && rows[0].name) {
      return {
        paramName:   desc.name,
        value:       rows[0].name,
        source:      "graph_lookup",
        resolvedVia: "fulltext" as const,
        entityLabel: desc.label,
        description: rows[0].description,
        matchedTerm: desc.query,
        score:       rows[0].score,
      };
    }
    // Fall through to vector search if full-text found nothing
  }

  // Per-label vector index (semantic labels, or full-text fallback)
  const indexName = LABEL_INDEX[desc.label];
  if (!indexName) return null;

  const queryEmbedding = await embedText(desc.query);
  const rows = await runQuery<{ name: string; description: string; score: number }>(
    `CALL db.index.vector.queryNodes('${indexName}', 1, $embedding)
     YIELD node, score
     WHERE NOT node:GenericLocation
     RETURN coalesce(node.canonicalName, node.name) AS name,
            coalesce(node.embeddingDescription, '') AS description,
            score`,
    { embedding: queryEmbedding }
  ).catch(() => []);

  if (!rows.length || !rows[0].name) return null;

  return {
    paramName:   desc.name,
    value:       rows[0].name,
    source:      "graph_lookup",
    resolvedVia: "vector" as const,
    entityLabel: desc.label,
    description: rows[0].description,
    matchedTerm: desc.query,
    score:       rows[0].score,
  };
}

async function executeCypher(
  cypher: string,
  params: Record<string, unknown> = {}
): Promise<{ results: Record<string, unknown>[]; error?: string }> {
  try {
    const results = await runQuery<Record<string, unknown>>(cypher, params);
    return { results };
  } catch (err: unknown) {
    return { results: [], error: (err as Error).message };
  }
}

// ── Tool execution ─────────────────────────────────────────────────────────────

async function runVectorSearch(
  input: string,
  embedding: number[],
  callId: string
): Promise<ToolCallResult> {
  try {
    const raw = await vectorSearch(embedding, 6);
    const chunkIds = raw.map((c) => c.chunkId);

    // Fetch entities and relationships for the retrieved chunks
    const entityRows = await runQuery<{ chunkId: string; entities: GraphEntity[] }>(
      `UNWIND $ids AS cid
       MATCH (c:Chunk {chunkId: cid})
       OPTIONAL MATCH (e)-[:MENTIONED_IN]->(c)
       WHERE NOT e:GenericLocation AND NOT e:Chunk
       WITH c.chunkId AS chunkId,
            collect(DISTINCT { label: head(labels(e)), name: coalesce(e.canonicalName, e.name, '') }) AS entities
       RETURN chunkId, entities`,
      { ids: chunkIds }
    );
    const entityMap = new Map(entityRows.map((r) => [r.chunkId, r.entities ?? []]));

    const relRows = await runQuery<GraphRelationship>(
      `UNWIND $ids AS cid
       MATCH (a)-[:MENTIONED_IN]->(c:Chunk {chunkId: cid})
       WHERE NOT a:GenericLocation AND NOT a:Chunk
       MATCH (a)-[r]->(b)
       WHERE type(r) <> 'MENTIONED_IN' AND NOT b:Chunk AND NOT b:GenericLocation
         AND EXISTS { MATCH (b)-[:MENTIONED_IN]->(c2:Chunk) WHERE c2.chunkId IN $ids }
       RETURN DISTINCT head(labels(a)) AS fromType, coalesce(a.canonicalName, a.name, '') AS from,
                       type(r) AS rel, head(labels(b)) AS toType, coalesce(b.canonicalName, b.name, '') AS to
       LIMIT 30`,
      { ids: chunkIds }
    );

    const chunks: SourceChunk[] = raw.map((c) => ({
      ...c,
      retrieval: "vector" as const,
      entities: entityMap.get(c.chunkId) ?? [],
    }));

    const allEntityMap = new Map<string, GraphEntity>();
    for (const c of chunks) {
      for (const e of c.entities) {
        if (e.name) allEntityMap.set(`${e.label}:${e.name}`, e);
      }
    }

    return {
      callId,
      tool: "vector_search",
      input,
      chunks,
      graph: { entities: [...allEntityMap.values()], relationships: relRows },
    };
  } catch (err: unknown) {
    return { callId, tool: "vector_search", input, error: (err as Error).message };
  }
}

async function runSequenceContext(
  input: string,
  embedding: number[],
  callId: string
): Promise<ToolCallResult> {
  try {
    // Find anchor chunks via vector search
    const anchors = await vectorSearch(embedding, 3);
    const anchorIds = anchors.map((c) => c.chunkId);
    const allIds = anchors.map((c) => c.chunkId);

    // Walk NEXT_CHUNK backward to surface preceding entries
    const preceding = await runQuery<{
      chunkId: string; text: string; date: string | null; author: string | null;
    }>(
      `UNWIND $anchorIds AS anchorId
       MATCH (prev:Chunk)-[:NEXT_CHUNK*1..5]->(anchor:Chunk {chunkId: anchorId})
       WHERE NOT prev.chunkId IN $allIds
       RETURN DISTINCT prev.chunkId AS chunkId, prev.text AS text,
                       prev.date AS date, prev.author AS author
       ORDER BY prev.date ASC
       LIMIT 8`,
      { anchorIds, allIds }
    );

    const combinedIds = [...allIds, ...preceding.map((r) => r.chunkId)];

    // Fetch entities for all chunks
    const entityRows = await runQuery<{ chunkId: string; entities: GraphEntity[] }>(
      `UNWIND $ids AS cid
       MATCH (c:Chunk {chunkId: cid})
       OPTIONAL MATCH (e)-[:MENTIONED_IN]->(c)
       WHERE NOT e:GenericLocation AND NOT e:Chunk
       WITH c.chunkId AS chunkId,
            collect(DISTINCT { label: head(labels(e)), name: coalesce(e.canonicalName, e.name, '') }) AS entities
       RETURN chunkId, entities`,
      { ids: combinedIds }
    );
    const entityMap = new Map(entityRows.map((r) => [r.chunkId, r.entities ?? []]));

    // Relationships
    const relRows = await runQuery<GraphRelationship>(
      `UNWIND $ids AS cid
       MATCH (a)-[:MENTIONED_IN]->(c:Chunk {chunkId: cid})
       WHERE NOT a:GenericLocation AND NOT a:Chunk
       MATCH (a)-[r]->(b)
       WHERE type(r) <> 'MENTIONED_IN' AND NOT b:Chunk AND NOT b:GenericLocation
         AND EXISTS { MATCH (b)-[:MENTIONED_IN]->(c2:Chunk) WHERE c2.chunkId IN $ids }
       RETURN DISTINCT head(labels(a)) AS fromType, coalesce(a.canonicalName, a.name, '') AS from,
                       type(r) AS rel, head(labels(b)) AS toType, coalesce(b.canonicalName, b.name, '') AS to
       LIMIT 30`,
      { ids: combinedIds }
    );

    const vectorChunks: SourceChunk[] = anchors.map((c) => ({
      ...c,
      retrieval: "vector" as const,
      entities: entityMap.get(c.chunkId) ?? [],
    }));
    const seqChunks: SourceChunk[] = preceding.map((c) => ({
      ...c,
      date: toDateStr(c.date),
      score: 0,
      retrieval: "sequence" as const,
      entities: entityMap.get(c.chunkId) ?? [],
    }));

    const allEntityMap = new Map<string, GraphEntity>();
    for (const c of [...vectorChunks, ...seqChunks]) {
      for (const e of c.entities) {
        if (e.name) allEntityMap.set(`${e.label}:${e.name}`, e);
      }
    }

    return {
      callId,
      tool: "sequence_context",
      input,
      chunks: [...vectorChunks, ...seqChunks],
      graph: { entities: [...allEntityMap.values()], relationships: relRows },
    };
  } catch (err: unknown) {
    return { callId, tool: "sequence_context", input, error: (err as Error).message };
  }
}

// After a cypher query, look up the result values as real graph nodes and find
// relationships between them so the NVL visualization has something to render.
async function enrichCypherGraph(
  results: Record<string, unknown>[]
): Promise<GraphContext> {
  // Collect every non-empty string value from the result rows
  const nameSet = new Set<string>();
  for (const row of results) {
    for (const val of Object.values(row)) {
      if (typeof val === "string" && val.trim()) nameSet.add(val.trim());
    }
  }
  if (nameSet.size === 0) return { entities: [], relationships: [] };

  const names = [...nameSet];

  // Resolve names to graph nodes (covers canonicalName AND Taxon.name)
  const entityRows = await runQuery<GraphEntity>(
    `UNWIND $names AS name
     MATCH (n) WHERE coalesce(n.canonicalName, n.name) = name
       AND NOT n:Chunk AND NOT n:GenericLocation
     RETURN DISTINCT head(labels(n)) AS label,
            coalesce(n.canonicalName, n.name) AS name
     LIMIT 60`,
    { names }
  );
  if (entityRows.length === 0) return { entities: [], relationships: [] };

  const entityNames = entityRows.map((e) => e.name);

  // Find relationships between any of these nodes
  const relRows = await runQuery<GraphRelationship>(
    `UNWIND $names AS name
     MATCH (a) WHERE coalesce(a.canonicalName, a.name) = name
       AND NOT a:Chunk AND NOT a:GenericLocation
     MATCH (a)-[r]->(b)
     WHERE type(r) <> 'MENTIONED_IN' AND NOT b:Chunk AND NOT b:GenericLocation
       AND coalesce(b.canonicalName, b.name) IN $names
     RETURN DISTINCT
       head(labels(a)) AS fromType, coalesce(a.canonicalName, a.name) AS from,
       type(r)         AS rel,
       head(labels(b)) AS toType,  coalesce(b.canonicalName, b.name) AS to
     LIMIT 40`,
    { names: entityNames }
  );

  return {
    entities: entityRows,
    relationships: relRows,
  };
}

async function runCypherQuery(
  input: string,
  embedding: number[],
  callId: string
): Promise<ToolCallResult> {
  // ── Step 1: Generate Cypher + typed param descriptors in one LLM call ────────
  const plan = await generateCypherPlan(input);

  // ── Step 2: Resolve each declared param against its per-label index only ─────
  // resolveParam embeds desc.query and searches only LABEL_INDEX[desc.label] —
  // no cross-label contamination (e.g. "grasses" won't match a Person node).
  const anchors = (
    await Promise.all(plan.params.map((p) => resolveParam(p, embedding)))
  ).filter((a): a is AnchorParam => a !== null);

  // Build the Neo4j params map
  const params: Record<string, unknown> = {};
  for (const a of anchors) params[a.paramName] = a.value;

  // ── Step 3: Execute; on failure ask LLM to correct using the same params ─────
  let cypher = plan.cypher;
  let { results, error } = await executeCypher(cypher, params);

  if (error) {
    // Ask the model to fix only the Cypher syntax/logic — params stay the same
    const fixPlan = await generateCypherPlan(
      `${input}\n\nThe following Cypher failed with: ${error}\nCypher:\n${cypher}\nPlease fix it.`
    );
    cypher = fixPlan.cypher;
    const retry = await executeCypher(cypher, params);
    if (!retry.error) {
      const graph = await enrichCypherGraph(retry.results);
      return { callId, tool: "cypher_query", input, cypher,
               anchorParams: anchors, cypherResults: retry.results, graph };
    }
    return { callId, tool: "cypher_query", input, cypher,
             anchorParams: anchors, error: retry.error };
  }

  const graph = await enrichCypherGraph(results);
  return { callId, tool: "cypher_query", input, cypher,
           anchorParams: anchors, cypherResults: results, graph };
}

// ── OpenAI tool definitions ───────────────────────────────────────────────────

const TOOLS: OpenAI.Chat.ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "vector_search",
      description:
        "Search journal passages by semantic similarity. Best for narrative questions, " +
        "'what did X write about Y', open-ended questions that need prose context.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "The search query" },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "sequence_context",
      description:
        "Find journal entries surrounding a key event using NEXT_CHUNK graph traversal. " +
        "Best for 'what happened before/after X', timeline or sequence questions.",
      parameters: {
        type: "object",
        properties: {
          event_description: {
            type: "string",
            description: "Description of the anchor event to locate in the journals",
          },
        },
        required: ["event_description"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "cypher_query",
      description:
        "Generate and execute a Cypher graph query. Best for structured questions: " +
        "relationships between entities, counting, aggregation, taxonomy, " +
        "'which X did Y do', 'list all X that Y', 'how many X'.",
      parameters: {
        type: "object",
        properties: {
          question: {
            type: "string",
            description: "The question to answer with a graph query",
          },
        },
        required: ["question"],
      },
    },
  },
];

const AGENT_SYSTEM = `You are a retrieval agent for the Lewis & Clark Expedition knowledge graph.
For each question, call the best tool(s). Guidelines:
- vector_search: "what did X write/say about Y", open narrative questions needing journal prose
- sequence_context: "what was happening in the days around event X" — retrieves the actual
  journal entries immediately surrounding an event; good for narrative context, NOT for lists
- cypher_query: any question that wants a structured list, counts, dates, or relationships —
  including "what places/species/people [before/after event X]", "list all X that Y",
  "how many", "which X did Y do", taxonomy, or any question asking for items WITH dates

When a question asks for a list of entities (places, species, people) with dates, always use
cypher_query — it can filter by date using Chunk.date and MENTIONED_IN relationships.
sequence_context cannot retrieve the full list and will miss most answers.

Call only what you need. For simple narrative questions, one vector_search is enough.

IMPORTANT: After receiving tool results, answer using ONLY the information returned by the
tools. Do not add facts, dates, names, or events from your training knowledge. If the
retrieved context does not contain enough information to answer the question fully, say so.`;

// ── Agent loop ────────────────────────────────────────────────────────────────

export async function* runAgent(
  question: string,
  embedding: number[],
  history: { role: "user" | "assistant"; content: string }[]
): AsyncGenerator<AgentEvent> {
  // Step 1: Ask LLM which tools to call
  const agentMessages: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: AGENT_SYSTEM },
    ...history.slice(-6),
    { role: "user", content: question },
  ];

  const toolSelection = await openai.chat.completions.create({
    model: process.env.OPENAI_CHAT_MODEL ?? "gpt-4o",
    messages: agentMessages,
    tools: TOOLS,
    tool_choice: "auto",
    temperature: 0,
  });

  const assistantMsg = toolSelection.choices[0].message;
  const toolCalls = assistantMsg.tool_calls ?? [];

  // If no tools called (shouldn't happen but be safe), fall back to vector search
  const effectiveCalls =
    toolCalls.length > 0
      ? toolCalls
      : [
          {
            id: "fallback",
            type: "function" as const,
            function: { name: "vector_search", arguments: JSON.stringify({ query: question }) },
          },
        ];

  // Step 2: Execute tools, streaming events as we go
  const results: ToolCallResult[] = [];

  for (const tc of effectiveCalls) {
    const toolName = tc.function.name as ToolName;
    const args = JSON.parse(tc.function.arguments) as Record<string, string>;
    const input =
      args.query ?? args.event_description ?? args.question ?? question;

    yield { type: "tool_start", callId: tc.id, tool: toolName, input };

    let result: ToolCallResult;
    if (toolName === "vector_search") {
      result = await runVectorSearch(input, embedding, tc.id);
    } else if (toolName === "sequence_context") {
      result = await runSequenceContext(input, embedding, tc.id);
    } else {
      result = await runCypherQuery(input, embedding, tc.id);
    }

    results.push(result);
    yield { type: "tool_result", result };
  }

  // Step 3: Aggregate sources for the UI
  const allChunks: SourceChunk[] = [];
  const entityMap = new Map<string, GraphEntity>();
  const relMap = new Map<string, GraphRelationship>();

  for (const r of results) {
    for (const c of r.chunks ?? []) {
      if (!allChunks.find((x) => x.chunkId === c.chunkId)) allChunks.push(c);
    }
    for (const e of r.graph?.entities ?? []) {
      if (e.name) entityMap.set(`${e.label}:${e.name}`, e);
    }
    for (const rel of r.graph?.relationships ?? []) {
      relMap.set(`${rel.from}:${rel.rel}:${rel.to}`, rel);
    }
  }

  yield {
    type: "sources",
    chunks: allChunks,
    graph: { entities: [...entityMap.values()], relationships: [...relMap.values()] },
    toolCalls: results,
  };

  // Step 4: Build context for final answer
  const contextParts: string[] = [];

  for (const r of results) {
    if (r.error) {
      contextParts.push(`[${r.tool} failed: ${r.error}]`);
      continue;
    }
    if (r.tool === "cypher_query" && r.cypherResults) {
      contextParts.push(
        `=== CYPHER QUERY RESULTS (${r.input}) ===\n` +
          (r.cypherResults.length === 0
            ? "No results returned."
            : r.cypherResults.map((row) => JSON.stringify(row)).join("\n"))
      );
    }
    if ((r.tool === "vector_search" || r.tool === "sequence_context") && r.chunks) {
      const vectorChunks = r.chunks.filter((c) => c.retrieval !== "sequence");
      const seqChunks = r.chunks.filter((c) => c.retrieval === "sequence");

      if (vectorChunks.length > 0) {
        contextParts.push(
          `=== JOURNAL PASSAGES (${r.tool}) ===\n` +
            vectorChunks
              .map((c) => {
                const hdr = [c.author, c.date].filter(Boolean).join(" · ");
                return `[${hdr || "passage"}]\n${c.text}`;
              })
              .join("\n\n---\n\n")
        );
      }
      if (seqChunks.length > 0) {
        const sorted = [...seqChunks].sort((a, b) =>
          (toDateStr(a.date) ?? "").localeCompare(toDateStr(b.date) ?? "")
        );
        contextParts.push(
          `=== PRECEDING JOURNAL ENTRIES (chronological) ===\n` +
            sorted
              .map((c) => {
                const hdr = [c.author, c.date].filter(Boolean).join(" · ");
                return `[${hdr || "entry"}]\n${c.text}`;
              })
              .join("\n\n---\n\n")
        );
      }
      const rels = r.graph?.relationships ?? [];
      if (rels.length > 0) {
        contextParts.push(
          `=== GRAPH RELATIONSHIPS ===\n` +
            rels.map((rel) => `${rel.from} -[${rel.rel}]-> ${rel.to}`).join("\n")
        );
      }
    }
  }

  // Step 5: Stream final answer
  const finalMessages: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: AGENT_SYSTEM },
    ...history.slice(-6),
    { role: "user", content: question },
    assistantMsg,
    ...effectiveCalls.map((tc, i) => ({
      role: "tool" as const,
      tool_call_id: tc.id,
      content: contextParts[i] ?? "(no result)",
    })),
  ];

  const stream = await openai.chat.completions.create({
    model: process.env.OPENAI_CHAT_MODEL ?? "gpt-4o",
    temperature: 0.2,
    messages: finalMessages,
    stream: true,
  });

  for await (const chunk of stream) {
    const token = chunk.choices[0]?.delta?.content;
    if (token) yield { type: "token", content: token };
  }

  yield { type: "done" };
}
