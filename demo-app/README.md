# Lewis & Clark GraphRAG Demo

A chatbot that demonstrates the difference between **vector-only RAG** and **vector + knowledge graph RAG**, using the Lewis & Clark Expedition journals as the corpus.

Built with [Next.js](https://nextjs.org), [Neo4j](https://neo4j.com), and [OpenAI](https://openai.com).

## What it shows

Toggle between two retrieval modes in real time:

| Mode | What happens |
|---|---|
| **Vector only** | The question is embedded and the most similar journal passages are retrieved. The LLM synthesises an answer from the text. |
| **Vector + Graph** | Same retrieval, plus the knowledge graph is traversed to pull named entities (people, places, species) and the relationships between them. The LLM gets structured facts alongside the raw text. |

The **Sources** panel on the right shows exactly what each mode sends to the LLM, so you can see the difference.

## Prerequisites

1. **Neo4j Desktop** (or AuraDB) with the Lewis & Clark knowledge graph loaded.  
   Run the build pipeline in the parent directory: `python3 build_graph.py`
2. **Node.js** 18+
3. An **OpenAI API key**

## Setup

```bash
cd demo-app
npm install
cp .env.local.example .env.local   # then fill in your values
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Environment variables

| Variable | Description |
|---|---|
| `NEO4J_URI` | Bolt URI, e.g. `bolt://127.0.0.1:7687` |
| `NEO4J_USER` | Database username |
| `NEO4J_PASSWORD` | Database password |
| `OPENAI_API_KEY` | Your OpenAI key |
| `OPENAI_EMBEDDING_MODEL` | Default: `text-embedding-3-small` |
| `OPENAI_CHAT_MODEL` | Default: `gpt-4o` |

## Adapting to your own dataset

1. Load your own data into Neo4j and create a vector index named `chunk_embeddings` on `Chunk` nodes.
2. Update the graph traversal queries in `lib/search.ts` to match your schema.
3. Update the example questions in `app/page.tsx`.
4. Update the entity label colours in `components/SourcePanel.tsx`.
