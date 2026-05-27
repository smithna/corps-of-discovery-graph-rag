# Lewis & Clark GraphRAG

A knowledge graph built from the Lewis & Clark Expedition journals, paired with a demo app that puts **vector RAG** and **graph RAG** side by side so you can compare them on real historical questions.

The journals are a good benchmark corpus: they span 8,000 miles and two years, introduce hundreds of people, places, species, and Native nations, and include rampant spelling variation and aliases — exactly the cases where a knowledge graph earns its keep.

---

## Repository layout

```
.
├── build_graph.py          # Pipeline entry point — run this to build the graph
├── ingest.py               # Fetch journal text, chunk, embed, load Chunk nodes
├── extract.py              # LLM entity + relationship extraction
├── resolve_mentions.py     # Resolve single-word person mentions to canonical names
├── fix_waterbody_labels.py # Reclassify mistyped Place nodes as WaterBody
├── flag_generic_locations.py  # Mark generic location phrases (e.g. "the river")
├── enrich_sacagawea.py     # Curated enrichment for Sacagawea's aliases
├── add_taxonomy.py         # GBIF taxonomy enrichment for Species nodes
├── cleanup_relationships.py   # Remove invalid / reversed relationships
├── disambiguate.py         # Merge duplicate entity nodes (two-pass)
├── setup_fulltext_indexes.py  # Full-text indexes for name search
├── embed_entities.py       # Embed entity canonical names for similarity search
├── requirements.txt
├── .env.example
├── demo-app/               # Next.js side-by-side comparison app
└── docs/                   # Talk slides, outline, and demo question bank
```

---

## Prerequisites

- **Python 3.10+**
- **Neo4j 5.x** — Desktop, AuraDB, or a local instance
- **OpenAI API key** — used for embeddings (`text-embedding-3-small`) and extraction (`gpt-4o-mini`)

---

## Setup

```bash
# 1. Clone and create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — fill in NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, OPENAI_API_KEY
```

---

## Building the graph

```bash
python build_graph.py
```

Full pipeline (~45 min depending on API concurrency):

| Step | Script | What it does |
|------|--------|--------------|
| 1 | `ingest.py` | Fetches journal text from the University of Nebraska digital archive, splits into ~500-token chunks, embeds each chunk, loads as `Chunk` nodes |
| 2 | `extract.py` | Runs GPT-4o-mini over every chunk to extract `Person`, `NativeNation`, `Place`, `WaterBody`, `AnimalSpecies`, `PlantSpecies`, `Supply`, and `Event` nodes plus 13 relationship types |
| 3 | `resolve_mentions.py` | Links single-word mentions (e.g. "Lewis") to their canonical `Person` node |
| 4 | `fix_waterbody_labels.py` | Reclassifies `Place` nodes whose names contain water terms |
| 5 | `flag_generic_locations.py` | Marks phrases like "the river" or "the bank" as `:GenericLocation` so they are excluded from graph traversal |
| 6 | `enrich_sacagawea.py` | Manually links Sacagawea's many aliases ("Janey", "the interpreter's wife", "the Indian woman") to her canonical `Person` node |
| 7 | `add_taxonomy.py` | Looks up each Species node in the GBIF API and adds `Taxon` nodes for genus, family, order, class, and phylum |
| 8 | `cleanup_relationships.py` | Deletes relationships whose source/target labels don't match the schema; flips any that were extracted backwards |
| 9–10 | `disambiguate.py` | Two-pass merge of duplicate entity nodes (e.g. "Meriwether Lewis" + "Capt. Lewis" → single node with aliases) |
| 11 | `setup_fulltext_indexes.py` | Creates full-text indexes on `canonicalName` and `aliases` for fast name lookup |
| 12 | `embed_entities.py` | Embeds each entity's canonical name so entities can be retrieved by semantic similarity |

### Re-running extraction only

If you want to improve entity extraction without re-embedding the corpus (saves API cost):

```bash
python build_graph.py --skip-ingest
```

This keeps existing `Chunk` nodes and re-runs steps 2–12.

---

## Knowledge graph schema

### Node labels

| Label | Description |
|-------|-------------|
| `Chunk` | A ~500-token passage from the journals, with embedding and date |
| `Person` | Individual people (expedition members, Native leaders, traders) |
| `NativeNation` | Native American nations and tribal groups |
| `Place` | Named land locations (forts, mountains, camps) |
| `WaterBody` | Named water features (rivers, creeks, falls, ocean) |
| `AnimalSpecies` | Animal species observed or described |
| `PlantSpecies` | Plant species observed or described |
| `Supply` | Food, goods, equipment, and trade items |
| `Event` | Named historical events (battles, deaths, ceremonies) |

### Relationship types

| Relationship | Source → Target | Description |
|---|---|---|
| `MENTIONED_IN` | KGEntity → Chunk | Entity appears in this journal passage |
| `MEMBER_OF` | Person → NativeNation | Cultural/ethnic tribal membership |
| `VISITED` | Person → Place/WaterBody | Traveled to or through |
| `CAMPED_AT` | Person → Place/WaterBody | Made camp at this location |
| `MET_WITH` | Person/NativeNation ↔ Person/NativeNation | Direct encounter |
| `OBSERVED` | Person → Species/Place/WaterBody | Saw, noted, or formally described |
| `TRADED_WITH` | Person/NativeNation ↔ Person/NativeNation | Exchanged goods |
| `ACQUIRED_PROVISION` | Person/NativeNation → Supply | Obtained a specific supply item |
| `GUIDED` | Person → Person | Led or navigated for |
| `INTERPRETED_FOR` | Person → NativeNation | Provided translation |
| `NAMED` | Person → Place/WaterBody | Gave this feature its name |
| `ORIGINATED_FROM` | Person/NativeNation → NativeNation/Place/WaterBody | Place of origin |
| `PARTICIPATED_IN` | Person/NativeNation → Event | Took part in this event |
| `BELONGS_TO` | Species → Taxon | Taxonomic classification |

---

## Demo app

See [`demo-app/README.md`](demo-app/README.md) for setup and environment variables.

```bash
cd demo-app
npm install
cp .env.local.example .env.local
npm run dev
```

---

## Talk materials

The [`docs/`](docs/) folder contains:

- **`rag_relationship_problem.pptx`** — talk slides ("RAG Has a Relationship Problem")
- **`talk_outline.md`** — full narrative outline with timing notes
- **`demo_questions.md`** — bank of questions that showcase graph vs. vector RAG
- **`build_deck.js`** — original pptxgenjs script used to generate the initial slide deck
