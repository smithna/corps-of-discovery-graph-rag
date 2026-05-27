# RAG Has a Relationship Problem
### 30-Minute Talk Outline — Community Days, Kansas City

---

## 0:00 — Hook: The Confident Wrong Answer (3 min)

Open with a failure mode everyone in the room has hit.

> "You've built a RAG pipeline. It retrieves relevant chunks, stuffs them into a prompt, and most of the time it works. Until it doesn't."

Tell the story of a RAG system confidently stitching together pieces that don't belong together:

- A legal AI that cites a policy that was superseded two years ago — both documents score highly for the query
- A medical chatbot that answers "can I take drug A with drug B?" by finding chunks about A and chunks about B separately, with no knowledge that the two are contraindicated *together*
- An internal knowledge base that misses that "Michael S." in one document and "Mike Sullivan" in another are the same person

**The punchline:** The problem usually isn't your embeddings or your model. It's that **vector search finds things that look similar to your question — not things that are connected to each other.**

Tease the resolution: *We're going to look at what happens when you give your retrieval pipeline connective tissue.*

---

## 0:03 — What Even Is a Graph? (5 min)

"No prior experience required" — and mean it. Start from scratch.

### The one-sentence definition
A graph is just **nodes and relationships**. That's it.

```
(Meriwether Lewis)-[:OBSERVED]->(Grizzly Bear)
(Grizzly Bear)-[:BELONGS_TO]->(Carnivora)
(Carnivora)-[:BELONGS_TO]->(Mammalia)
```

### The SQL analogy — meet people where they are
If most of your audience comes from SQL (and for a Community Days crowd, they do):

| SQL | Graph |
|---|---|
| Table | Node label |
| Row | Node |
| Foreign key | Relationship |
| JOIN | Traversal |

The difference: in SQL, relationships are implicit — you figure them out at query time by matching IDs across tables. In a graph, relationships are **first-class citizens** stored explicitly with the data.

### Why that matters for retrieval
Vector search answers: *"What content looks like this question?"*  
Graph traversal answers: *"What is connected to this thing?"*

Both are useful. Neither replaces the other.

---

## 0:08 — How It's Built (4 min)

*Be honest about implementation complexity. The audience will respect it.*

### The pipeline (show a simple diagram)

```
User question
     │
     ▼
 Embed (OpenAI text-embedding-3-small)
     │
     ├──────────────────────────────────────────────┐
     ▼                                              ▼
Vector RAG                                     Graph RAG Agent
     │                                              │
Vector search                              ┌── Tool loop ──┐
(Chunk embeddings)                         │               │
     │                               vector_search  cypher_query
     │                               (Chunk index)  (1. LLM generates Cypher
     │                                      │           + typed param list
     │                                      │        2. each param resolved
     │                                      │           against per-label index
     │                                      │           → Neo4j)
     │                               sequence_context       │
     │                               (NEXT_CHUNK walk)      │
     │                                      └───────┬───────┘
     │                                              │
     ▼                                              ▼
 LLM answer                                   LLM answer
 (vector context)                             (graph context)
```

### What's in the graph
- **Chunk nodes** with embeddings (the same chunks vector search uses)
- **Entity nodes**: Person, Place, WaterBody, AnimalSpecies, PlantSpecies, NativeNation, Event
- **Taxon nodes**: taxonomy hierarchy (genus → family → order → class…) with common-name aliases aggregated from descendant species
- **Relationships**: MENTIONED_IN, CAMPED_AT, OBSERVED, TRADED_WITH, BELONGS_TO, NEXT_CHUNK — all activity relationships carry `{date, chunkId}` so temporal queries filter on the relationship itself, not on chunk text
- **`:GenericLocation` label** to filter out "CAMP", "ISLAND", "VALLEY" — because not everything the LLM extracts is useful
- **Per-label entity indexes** — every entity node has an `embeddingDescription` (human-readable, includes aliases) and a vector embedding. Proper-noun types (Person, Place, WaterBody, NativeNation) each have a dedicated full-text index (`person_search`, `location_search` covering both Place and WaterBody, `native_nation_search`) for exact token matching. Semantic types (AnimalSpecies, PlantSpecies, Taxon, Event) each have a dedicated vector index (`taxon_embeddings`, `event_embeddings`, etc.) for concept matching. Keeping indexes per-label eliminates cross-label noise — a query for "grasses" only searches PlantSpecies and Taxon nodes, never Person.

### What it took to build
Be real about the steps:

1. **Ingest** — fetch HTML, chunk by journal entry, embed, store as Chunk nodes (~1 hour of work, ~$5 in embedding cost for this corpus)
2. **Extract** — LLM-based entity + relationship extraction from each chunk (the expensive step — this is where `gpt-4o-mini` paid for itself; ~$8 for the full corpus)
3. **Disambiguate** — "CLARK", "CAPTAIN CLARK", and "WILLIAM CLARK" are the same node (two-pass LLM disambiguation)
4. **Enrich** — taxonomy from GBIF, Sacagawea references from an annotated source (the interesting hard problems)

   > **The graph build is your best opportunity to join structured external data to your unstructured corpus.** In a vector store, you'd have to flatten this into chunk metadata — and that only works for simple cases. Sacagawea *could* be a metadata tag, but you'd need to pre-process every chunk to detect all her aliases first, and you'd still lose her relationships to other people. Taxonomy breaks down entirely: "what rodents?" requires knowing which species fall under Rodentia — knowledge that has to live somewhere. Your options are to filter on a hardcoded list, tag every chunk with every taxonomy level (arrays at every level, decided at index time), or pre-expand boolean flags that explode combinatorially. In the graph, the hierarchy *is* the index — `BELONGS_TO*` traverses it in one token, and the enrichment data lands in a structure that can actually express its relationships.

5. **Curate** — manually assert facts the LLM could never reliably extract (details below)
6. **Flag** — `:GenericLocation` label to make place queries useful
7. **Embed entities** — generate natural-language descriptions for every entity and Taxon node (`embed_entities.py`), embed them with the same model used for chunks, and store per-label vector indexes. Create per-label full-text indexes for proper-noun types (`setup_fulltext_indexes.py`). Separate indexes by label so anchor resolution at query time never contaminates across types — a taxon lookup searches only `taxon_embeddings`, a person lookup searches only `person_search`.

### The graph is an editable artifact

This is one of the most important practical differences between a knowledge graph and a vector store, and it rarely gets discussed.

When an LLM extracts knowledge into a vector store, the result is a set of floating-point vectors — opaque, immutable, and impossible to patch without re-embedding. If the LLM got something wrong, or missed something entirely, you have no recourse except to change the source text and re-index.

A knowledge graph is **inspectable and correctable**. You can open it in a browser, see every node and relationship, identify errors, and fix them with a Cypher query. Subject matter experts — historians, domain scientists, legal reviewers — can validate the extracted knowledge and make targeted corrections that persist across queries.

**The Sacagawea example makes this concrete.**

She is one of the most historically significant members of the expedition, but she is almost never named directly in the journals. Clark calls her "the Indian woman," "the interpreter's wife," "the squar," and occasionally "Janey." The LLM extracts all of those as separate entities or misses them entirely — it has no way to know they all refer to the same person without external knowledge.

In a vector store, your only option is to hope that "Janey" and "Sacagawea" embed close enough to match — and for passages that don't name her at all, there is no signal to work with.

In the knowledge graph, we:
1. Create a single canonical `Person` node for Sacagawea
2. Use a curated external annotation list (from lewis-clark.org) that tags which journal entries she appears in
3. Run a matching step that links her node to the right Chunk nodes via `MENTIONED_IN`, using date + string similarity to confirm each match
4. Extract her relationships — `INTERPRETED_FOR`, `GUIDED`, `MET_WITH` — from those chunks

The result: every query involving Sacagawea resolves to one node, regardless of what name appears in the source text. That's not something an LLM can do at extraction time, and it's not something a vector store can represent at all.

**The broader principle:** a knowledge graph gives subject matter experts a correction surface. The LLM does the bulk extraction — that's the part that would take a human years. The expert does the targeted fixes that require cultural or domain knowledge the LLM doesn't have. That division of labor is only possible when the extracted knowledge is structured and inspectable.

**Honest time estimate:** 2–3 days of engineering for a greenfield corpus of this size. For an enterprise knowledge base you already have chunked and embedded: probably 1 day to add the extraction and graph layer.

### The code is all in this repo
Point to the GitHub. Every script is a standalone Python file. The demo UI is a Next.js app in `demo-app/`. The hard parts are documented.

---

## 0:12 — Graph Tour: Neo4j Bloom (3 min)

*Don't explain Bloom — just use it. The goal is one "oh, that's what a knowledge graph looks like" moment before the chatbot demo.*

Now the audience knows what's in the graph. Show them what it looks like.

1. **Start at a person node** — open Lewis or Clark, expand one hop. OBSERVED, CAMPED_AT, MET_WITH relationships fan out. Point out that each edge is labelled — not just "connected to," but *how* connected.

2. **Follow a species node** — click one AnimalSpecies, expand its BELONGS_TO chain. The taxonomy tree appears visually: species → genus → family → order. This is the hierarchy the demo will query in a moment.

3. **Show a place cluster** — zoom out enough to see a cluster of Place/WaterBody nodes connected to multiple Person nodes via CAMPED_AT. Visually, it's obvious that multiple people were at the same place — something that isn't visible at all in a list of documents.

**Keep it to 2–3 minutes.** Bloom is a reveal, not a tutorial. As soon as the audience has the mental picture — nodes, relationships, structure — move to the chatbot.

---

## 0:15 — Live Demo: Lewis & Clark (10 min)

*Switch to the demo app. The Lewis & Clark Expedition journals are the corpus — 3 years of field notes from 1804–1806.*

**Why this corpus?** It has everything that makes RAG hard:
- Hundreds of people, places, and species mentioned inconsistently ("the squaw", "the interpreter's wife", "Janey" — all Sacagawea)
- Document structure that matters (what happened in the weeks *before* Sergeant Floyd died?)
- Taxonomy that requires hierarchy (what bird *families* did they encounter?)
- Aggregation across dozens of documents ("which Native Nations did they trade with?")

### How the demo works
The UI shows **Vector RAG and Graph RAG side by side** — both run simultaneously on every question. The left column is pure vector search; the right column is a tool-calling agent with three tools it can use in any combination:

- **`vector_search`** — semantic search over embedded journal chunks
- **`sequence_context`** — follows `NEXT_CHUNK` relationships to pull adjacent journal entries
- **`cypher_query`** — asks the LLM to generate both the Cypher *and* a typed parameter list in one call (Cypher-first); each declared parameter is then resolved against its matching per-label index: full-text for proper nouns (person_search, location_search), vector for semantic types (taxon_embeddings, event_embeddings). The LLM knows the type of every parameter before any lookup happens, so each search is scoped to exactly the right index.

The sources panel on the right shows exactly what the agent did: which tools it called, what Cypher it generated, how it resolved entity names to canonical graph nodes, and what passages it retrieved. Nothing is hidden.

---

### Demo Beat 1: A question vector alone handles fine (1 min)
Ask: *"What did Lewis write about grizzly bears?"*

Both columns give a good answer. The Graph RAG sources panel shows a `cypher_query` call resolving "grizzly bears" → `URSUS ARCTOS HORRIBILIS` via entity embedding lookup, but the answer isn't dramatically different from vector alone.

**Point:** Vector RAG was designed for this. If all your questions look like this, you might not need a graph.

---

### Demo Beat 2: A question that exposes the gap (3 min)
Ask: *"What species did Lewis observe near the Columbia River?"*

**Vector RAG column:** The LLM synthesises from text. It will get *some* species, but it's guessing from passage similarity — not computing from evidence. Ask the audience: *"How confident are you in this list? How would you verify it?"*

**Graph RAG column:** The agent generates the Cypher first, declaring `$person` (label: Person) and `$waterBody` (label: WaterBody) as typed parameters. "Lewis" resolves to `MERIWETHER LEWIS` via `person_search`; "Columbia River" resolves to `COLUMBIA RIVER` via `location_search` (the combined Place+WaterBody full-text index). The Cypher then traverses `OBSERVED` relationships. The answer is grounded in explicit relationships — not inferred from text similarity.

**Point:** The graph turns a fuzzy synthesis into a structured lookup. And crucially, you can *show your work* — the Cypher query in the sources panel is the audit trail.

---

### Demo Beat 3: The question vector can't answer at all (3 min)
Ask: *"What places did the corps visit in the three weeks before Sergeant Floyd died?"*

**Vector RAG column:** The LLM finds chunks *about* Floyd's death. But it cannot navigate backward through the timeline. The journal entries are a linked list (`NEXT_CHUNK` relationships). Vector search has no concept of "the document before this one."

**Graph RAG column:** The agent generates Cypher declaring `$event` (label: Event). "Death of Sergeant Floyd" resolves to the correct `Event` node via `event_embeddings` (the per-label vector index — full-text would miss this because the query is a concept, not a verbatim name). The Cypher derives the event date from its `MENTIONED_IN` chunk, then queries `(Person)-[r:VISITED|CAMPED_AT]->(place) WHERE r.date >= eventDate - duration({weeks: 3}) AND r.date < eventDate`. The answer is a dated list of campsites. Point to the `r.date` filter in the Cypher — that's the relationship's own date property, not guessed from chunk text.

**Point:** Some questions are structurally unanswerable with retrieval alone. The answer isn't in any single chunk — it's in the *structure* connecting chunks.

---

### Demo Beat 4: Taxonomy + Temporal (2 min)
Ask: *"What rodent species were mentioned before they reached the Rocky Mountains?"*

**Vector RAG column:** "Rodent" as a concept won't reliably surface the right chunks, and there's no way to enforce a date boundary — the answer will be vague or miss species entirely.

**Graph RAG column:** The agent generates Cypher declaring `$taxon` (label: Taxon) and `$place` (label: Place). "Rodents" resolves to `Rodentia` via `taxon_embeddings`; "Rocky Mountains" resolves to `GATES OF THE ROCKY MOUNTAINS` — the landmark Lewis himself named — via `location_search`. The Cypher walks `BELONGS_TO*` from all `AnimalSpecies` up to `Rodentia`, then filters `OBSERVED` relationships to those before the first mention date of that Place node. The results: beaver, prairie dog, gray squirrel, porcupine, muskrat. **Beaver is the talking point** — it was the primary economic motivation for the entire expedition.

**Point:** Two graph features in one query. `BELONGS_TO*` traversal answers "what is in this category" regardless of depth; relationship `r.date` answers "when" with precision. Neither is available in a document retrieval system.

---

### Demo Beat 5: Curation payoff — Sacagawea (1 min)
Ask: *"Which Native Nations did Sacagawea interpret for?"*

**Vector RAG column:** May return something, but it's drawing on passages that happen to mention both Sacagawea and a nation — the quality is unpredictable and there's no guarantee of completeness.

**Graph RAG column:** "Sacagawea" resolves to a single canonical `Person` node via `person_search`. The Cypher walks `INTERPRETED_FOR` relationships. The answer is a clean list grounded in explicit, curated relationships.

**The point to make:** This question only works because a human stepped in where the LLM couldn't. Every alias — "Janey", "the interpreter's wife", "the squar" — resolves to one node. Every passage she appears in, even when not named, is connected via `MENTIONED_IN`. The vector store has no equivalent — you can't manually assert identity in a set of floating-point vectors.

*This is also the opening to say: "If you have domain experts who can validate extracted knowledge, a graph gives them a correction surface. The LLM does the bulk lift; the expert patches the parts that require cultural knowledge the model doesn't have."*

---

## 0:25 — Why Not Just Use Postgres? (3 min)

*This question will come up. Answer it honestly — and early enough that skeptics stay with you.*

The short answer: if your "graph" is really just filtering vector results by metadata, Postgres + pgvector is the right call and you should use it. But there are three specific things a property graph does that SQL does awkwardly enough to matter.

### 1. Variable-depth traversal

The taxonomy demo query in Cypher:
```cypher
MATCH (s:AnimalSpecies)-[:BELONGS_TO*]->(ancestor)
RETURN ancestor.rank, ancestor.canonicalName
```

The SQL equivalent requires a recursive CTE — which most developers have never written, most query planners don't optimize well, and which has to know a maximum depth in advance. For a fixed, shallow hierarchy (like a three-tier org chart), SQL is fine. For an arbitrary-depth tree where you don't know how deep it goes until you query it, graphs win cleanly.

### 2. Relationships as the primary data structure

In SQL, a relationship between two entities is represented as a row in a junction table. When you have many entity types with many relationship types between them — in this demo: Person, Place, WaterBody, NativeNation, AnimalSpecies, PlantSpecies, connected by OBSERVED, VISITED, TRADED_WITH, MEMBER_OF, and seven more — you end up with a junction table explosion. In Neo4j, each relationship type is just a labelled edge. No schema migration required when you discover a new relationship type during extraction.

This matters especially for LLM-based extraction, because **you often don't know your schema before you start extracting**. A graph accommodates schema evolution naturally; a relational database makes you pay for every new relationship type up front.

### 3. Path queries

"Find everything connected to this entity within N hops" is the native query pattern of a graph database. It's how recommendation engines, fraud detection, and knowledge traversal all work. In SQL, each hop is a JOIN — and JOIN costs compound multiplicatively. A graph database stores relationships as direct pointers, so each traversal step is O(1) regardless of graph size. The query that takes 3 seconds in SQL at 2 hops takes 30 seconds at 4 hops. In Neo4j, the latency barely changes.

### 4. Text-to-query accuracy at runtime

If you're building a system where an LLM writes queries dynamically — an agent, a natural-language interface, a chatbot that generates its own retrieval logic — language legibility matters.

For simple lookups, text2SQL is reliable. LLMs have seen enormous amounts of SQL in training data. But the queries that justify adding a graph are exactly the ones text2SQL gets wrong most often:

- **Recursive CTEs** have a rigid structure (base case, recursive case, explicit termination) that LLMs generate incorrectly a surprising fraction of the time. The errors are often silent — the query runs but returns wrong results.
- **Multi-hop JOINs** require the LLM to correctly produce every intermediate table name, every foreign key column, and every ON condition — that's four places to make an error per hop.
- **Variable-depth paths** have no clean SQL expression at all without a recursive CTE — so the LLM is being asked to generate the construct it's worst at, for the query it's most needed for.

Cypher is structurally more aligned with how LLMs encode relationships. `(Lewis)-[:OBSERVED]->(Grizzly Bear)` reads like the sentence it represents. The model is translating natural language into something that looks like natural language. A recursive CTE looks nothing like the concept it expresses, and SQL has five or six ways to express a join (INNER JOIN, WHERE clause, subquery, EXISTS, CTE), giving the model more choices to get wrong.

**The compounding argument:** The queries hardest for text2SQL to generate correctly are the same queries hardest for SQL to express at all — and they're the exact queries you're building a graph to answer. If you need runtime query flexibility, language legibility and traversal expressiveness reinforce each other.

### Where SQL is the right answer

- You need to filter RAG results by structured metadata (department, date range, document type) — pgvector + SQL handles this perfectly and you likely already have the infrastructure
- Your "relationships" are fixed and shallow — a two-tier hierarchy, a simple foreign key — no need for a graph
- Your team knows SQL deeply and the graph would be a new dependency with no clear payoff

**The honest framing for the audience:** You're not replacing your relational database. You're adding a layer for the queries that make SQL uncomfortable.

---

## 0:25 — Decision Framework: Is Your Use Case a Good Fit? (4 min)

Not every RAG problem needs a graph. Here's how to think about it.

### Strong signal you need a graph

**"My answers require traversal, not retrieval."**  
Questions like: *what are all X that belong to category Y?*, *what happened before/after event Z?*, *who is connected to whom?*

**"The same entity appears under different names across documents."**  
Disambiguation at retrieval time is essentially impossible without a canonical node to resolve to.

**"Hierarchy or sequence is essential to the correct answer."**  
Org charts, taxonomies, process flows, policy supersession, document ordering.

**"I need to know I haven't missed anything."**  
Completeness guarantees. Vector search returns *similar* documents. Graph traversal returns *all* documents matching a structural condition.

---

### Graph probably overkill if...

- All questions are factual lookups ("what does section 4.2 say?") — vector RAG is great at this
- Your corpus is small (< a few hundred documents) — just put it all in context
- Your documents don't have meaningful entities or relationships to extract
- You need a working prototype by Friday

---

### The implementation question to ask first

*"What does my user need to know that isn't in any single document?"*

If the answer is "nothing — each question can be answered by a passage," vector RAG is sufficient.  
If the answer involves aggregation, sequence, hierarchy, or cross-document entity identity — you want a graph.

---

## 0:29 — Close: What You're Taking Home (1 min)

Three things:

1. **Mental model** — graphs store relationships explicitly; vector search finds similar things; neither replaces the other; together they cover different failure modes

2. **Realistic complexity** — adding a graph layer to an existing RAG pipeline is a 1–3 day project, not a rewrite; the extraction step is the interesting hard problem; disambiguation is usually worth the effort

3. **Decision signal** — if your users ask questions whose answers live in the *connections* between documents rather than in the documents themselves, a graph will pay for itself quickly

**The demo app and all build scripts are open-source in this repo. Fork it, swap in your own corpus, and see what questions become answerable.**

---

## Appendix: Backup Demo Questions

In case a demo beat doesn't land — questions with reliably interesting answers:

| Question | Why it's good |
|---|---|
| What bird families did the expedition encounter? | `BELONGS_TO*` taxonomy traversal — taxonomy is a graph problem; also a good demo of how the LLM might cheat with training data in the vector column |
| Which Native Nations did the expedition trade with? | Multi-hop: Person → TRADED_WITH → NativeNation |
| What plants did the expedition collect as food or medicine? | Taxonomy + OBSERVED relationships |
| What are the different names for George Drouillard in the journals? | Shows `p.aliases` lookup — good for illustrating entity disambiguation, but note this is a property lookup, not a traversal |
| Who was Sacagawea, and how does she appear in the journals? | Same `p.aliases` pattern — she appears under many names including "Janey" and "the interpreter's wife" |
| What animals were new to science at the time of the expedition? | DESCRIBED relationships + taxonomy |
| What were the most frequently mentioned places? | Aggregation over MENTIONED_IN counts |
| What species were observed in the week after the corps reached the Pacific? | Event anchor: resolves "reached the Pacific" → Event node via event_embeddings, then temporal Cypher on `r.date` |

---

## Timing Cheat Sheet

| Segment | Time | Cumulative |
|---|---|---|
| Hook: the confident wrong answer | 3 min | 0:03 |
| What is a graph? | 5 min | 0:08 |
| How it's built | 4 min | 0:12 |
| Graph tour: Neo4j Bloom | 3 min | 0:15 |
| Demo beat 1: grizzly bears (baseline) | 1 min | 0:16 |
| Demo beat 2: species near Columbia River | 3 min | 0:19 |
| Demo beat 3: places before Floyd died | 3 min | 0:22 |
| Demo beat 4: rodents before Rocky Mountains (taxonomy + temporal) | 2 min | 0:24 |
| Demo beat 5: Sacagawea (curation payoff) | 1 min | 0:25 |
| Why not just Postgres? | 2 min | 0:27 |
| Decision framework + close | 3 min | 0:30 |

---

## References & Further Reading

Point attendees to **[graphrag.com](https://graphrag.com)** as the canonical starting point — it has concept guides, how-to walkthroughs, and the full research index at [graphrag.com/appendices/research/](https://graphrag.com/appendices/research/).

### Papers worth calling out specifically

**For validating the hybrid approach (directly relevant to the demo):**

- **HybridRAG: Integrating Knowledge Graphs and Vector Retrieval Augmented Generation for Efficient Information Extraction** — arXiv 2408.04948  
  The empirical paper closest to what this demo shows. Combines graph-based and vector retrieval and measures the improvement. Good citation if anyone asks "but is there actual research on this?"

- **HybGRAG: Hybrid Retrieval-Augmented Generation on Textual and Relational Knowledge Bases** — arXiv 2412.16311  
  Addresses the specific problem of queries that span unstructured text *and* relational/graph knowledge — exactly the toggle this demo demonstrates.

**For the "why graph at all" argument:**

- **From Local to Global: A Graph RAG Approach to Query-Focused Summarization** — arXiv 2404.16130  
  The Microsoft Research paper that put GraphRAG on the map. Shows that graph structure enables *global* summarization queries ("what are the main themes across the entire corpus?") that vector search fundamentally cannot answer — because vector search finds similar passages, not aggregate patterns.

- **A Benchmark to Understand the Role of Knowledge Graphs on LLM Accuracy for Question Answering on Enterprise SQL Databases** — arXiv 2311.07509  
  Directly useful for the "why not just Postgres?" conversation. Empirical evidence that knowledge graphs improve LLM accuracy even when structured data is already available in SQL.

**For the curious — going deeper:**

- **Graph Retrieval-Augmented Generation: A Survey** — arXiv 2408.08921  
  The comprehensive overview if attendees want to map the whole landscape after the talk.

- **Retrieval-Augmented Generation with Knowledge Graphs for Customer Service Question Answering** — arXiv 2404.17723  
  Enterprise-focused case study — relatable for an audience building internal tooling.

- **Think-on-Graph: Deep and Responsible Reasoning of Large Language Model on Knowledge Graph** — arXiv 2307.07697  
  For attendees interested in how the LLM itself can be taught to *reason over* a graph, rather than just receiving graph-enriched context.
