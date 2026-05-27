#!/usr/bin/env python3
"""
Lewis & Clark Knowledge Graph — entity description + embedding.

For every entity node this script:

  1. Builds a natural-language description from the node's name and aliases.
     The description is stored as n.embeddingDescription so the demo UI can
     show exactly what was embedded, making the retrieval interpretable.

  2. Embeds the description with the same model used for Chunk nodes.

  3. Adds the :KGEntity label so all entity types share a single vector index.

  4. Creates the entity_embeddings vector index if it doesn't exist.

Covered node types
──────────────────
Standard entities (Person, Place, WaterBody, AnimalSpecies, PlantSpecies,
  NativeNation, Event) — descriptions built from n.name + n.aliases.

Taxon — descriptions built by aggregating common-name aliases from all
  descendant species via BELONGS_TO relationships.  This bridges queries
  like "salmon" or "bears" to Salmonidae / Ursidae without any LLM calls.

Event — descriptions built from n.name + n.aliases.  This allows semantic
  queries like "birth of a child" or "death of a corps member" to resolve
  to specific canonical event nodes (BIRTH OF CHARBONNEAU'S SON, etc.).

Usage:
  python embed_entities.py              # embed all entities
  python embed_entities.py --dry-run    # print sample descriptions, no writes
"""

import argparse
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv(dotenv_path=".env")

NEO4J_URI       = os.environ["NEO4J_URI"]
NEO4J_USER      = os.environ["NEO4J_USER"]
NEO4J_PASSWORD  = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]

EMBEDDING_MODEL  = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM    = int(os.getenv("EMBEDDING_DIM", "1536"))
EMBED_BATCH_SIZE = 100
WRITE_BATCH_SIZE = 200

# Standard entity labels — nodes that have canonicalName + aliases properties
ENTITY_LABELS = [
    "Person", "Place", "WaterBody",
    "AnimalSpecies", "PlantSpecies", "NativeNation", "Event",
]

# ── Description context strings ───────────────────────────────────────────────

LABEL_CONTEXT = {
    "Person":        "person associated with the Lewis and Clark Expedition",
    "Place":         "place documented in the Lewis and Clark Expedition journals",
    "WaterBody":     "body of water documented in the Lewis and Clark Expedition journals",
    "AnimalSpecies": "animal species observed or described during the Lewis and Clark Expedition",
    "PlantSpecies":  "plant species collected or described during the Lewis and Clark Expedition",
    "NativeNation":  "Native American nation encountered by the Lewis and Clark Expedition",
    "Event":         "event documented in the Lewis and Clark Expedition journals",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class EntityRecord:
    element_id: str
    label: str
    canonical_name: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    taxon_rank: str = ""        # only set for Taxon nodes
    description: str = ""
    embedding: list[float] = field(default_factory=list)


# ── Description builders ──────────────────────────────────────────────────────

def build_description(rec: EntityRecord) -> str:
    """
    Build a natural-language description for standard entity nodes.

    Structure:
      <DisplayName> — <label context>[. Also known as: <alias1>, ...]

    Aliases are deduplicated case-insensitively; the canonical name is
    excluded to avoid redundancy.  Up to 8 aliases kept for conciseness.
    """
    context = LABEL_CONTEXT.get(rec.label, "entity in the Lewis and Clark knowledge graph")
    base = f"{rec.display_name} — {context}"

    seen: set[str] = {rec.canonical_name.upper(), rec.display_name.upper()}
    clean_aliases: list[str] = []
    for alias in rec.aliases:
        alias = alias.strip()
        upper = alias.upper()
        if alias and upper not in seen and len(alias) > 1:
            seen.add(upper)
            clean_aliases.append(alias)

    if clean_aliases:
        base += ". Also known as: " + ", ".join(clean_aliases[:8])

    return base


def build_taxon_description(rec: EntityRecord) -> str:
    """
    Build a description for a Taxon node.

    The aliases on a Taxon record are common names aggregated from all
    descendant species — e.g. Salmonidae gets "salmon, trout, Chinook..."
    This lets queries like "salmon" or "bears" match the right Taxon.

    Structure:
      <Name> — <rank> (Taxon)[. Related species known as: <alias1>, ...]
    """
    rank = rec.taxon_rank or "taxon"
    base = f"{rec.display_name} — {rank} (Taxon) in the Lewis and Clark Expedition taxonomy"

    seen: set[str] = {rec.canonical_name.upper()}
    clean_aliases: list[str] = []
    for alias in rec.aliases:
        alias = alias.strip()
        upper = alias.upper()
        if alias and upper not in seen and len(alias) > 1:
            seen.add(upper)
            clean_aliases.append(alias)

    if clean_aliases:
        base += ". Related species known as: " + ", ".join(clean_aliases[:12])

    return base


# ── Neo4j fetch helpers ───────────────────────────────────────────────────────

def fetch_entities(driver) -> list[EntityRecord]:
    """Fetch standard entity nodes (canonicalName + aliases pattern)."""
    records: list[EntityRecord] = []
    labels_union = "|".join(ENTITY_LABELS)
    with driver.session() as s:
        result = s.run(
            f"""
            MATCH (n:{labels_union})
            WHERE NOT n:GenericLocation
            RETURN
              elementId(n) AS eid,
              // Use the most specific matching label; species nodes carry a
              // base :Species label that would otherwise sort first.
              [x IN labels(n) WHERE x IN $entityLabels][0] AS label,
              coalesce(n.canonicalName, n.name, '') AS canonical_name,
              coalesce(n.name, n.canonicalName, '') AS display_name,
              coalesce(n.aliases, [])               AS aliases
            ORDER BY label, canonical_name
            """,
            entityLabels=ENTITY_LABELS,
        )
        for row in result:
            records.append(EntityRecord(
                element_id    = row["eid"],
                label         = row["label"],
                canonical_name= row["canonical_name"],
                display_name  = row["display_name"],
                aliases       = list(row["aliases"]),
            ))
    return records


def fetch_taxons(driver) -> list[EntityRecord]:
    """
    Fetch Taxon nodes and aggregate common-name aliases from all descendant
    species via BELONGS_TO relationships.

    Taxon nodes use n.name / n.rank (no canonicalName / aliases properties),
    so they are fetched separately and handled with their own description builder.
    """
    with driver.session() as s:
        taxon_rows = list(s.run(
            """
            MATCH (t:Taxon)
            RETURN elementId(t) AS eid, t.name AS name, t.rank AS rank
            ORDER BY t.rank, t.name
            """
        ))

        # Collect aliases from every descendant species in one pass
        alias_rows = list(s.run(
            """
            MATCH (s)-[:BELONGS_TO*]->(t:Taxon)
            WHERE (s:AnimalSpecies OR s:PlantSpecies) AND NOT s:GenericLocation
            RETURN elementId(t) AS taxon_eid,
                   coalesce(s.aliases, []) AS aliases
            """
        ))

    # Build alias map: taxon element_id → set of alias strings
    alias_map: dict[str, set[str]] = defaultdict(set)
    for row in alias_rows:
        for alias in row["aliases"]:
            alias = alias.strip()
            if alias and len(alias) > 1:
                alias_map[row["taxon_eid"]].add(alias)

    records: list[EntityRecord] = []
    for row in taxon_rows:
        eid  = row["eid"]
        name = row["name"]
        rank = row["rank"] or ""
        records.append(EntityRecord(
            element_id    = eid,
            label         = "Taxon",
            canonical_name= name,
            display_name  = name,
            aliases       = sorted(alias_map.get(eid, set())),
            taxon_rank    = rank,
        ))
    return records


# ── Neo4j write helpers ───────────────────────────────────────────────────────

# One vector index per node label — the agent resolves each $param by searching
# only the index that matches the label declared in the Cypher plan, eliminating
# cross-label noise (e.g. grasshoppers appearing when searching for grass taxa).
LABEL_INDEXES: dict[str, str] = {
    "Person":        "person_embeddings",
    "Place":         "place_embeddings",
    "WaterBody":     "waterbody_embeddings",
    "AnimalSpecies": "animalspecies_embeddings",
    "PlantSpecies":  "plantspecies_embeddings",
    "NativeNation":  "nativenation_embeddings",
    "Event":         "event_embeddings",
    "Taxon":         "taxon_embeddings",
}


def create_vector_indexes(driver) -> None:
    """Create one vector index per node label."""
    with driver.session() as s:
        for label, index_name in LABEL_INDEXES.items():
            s.run(
                f"""
                CREATE VECTOR INDEX {index_name} IF NOT EXISTS
                FOR (n:{label}) ON (n.embedding)
                OPTIONS {{
                  indexConfig: {{
                    `vector.dimensions`: {EMBEDDING_DIM},
                    `vector.similarity_function`: 'cosine'
                  }}
                }}
                """
            )
            print(f"  {index_name} ready.")


def write_batch(driver, batch: list[EntityRecord]) -> None:
    """Write description, embedding, and :KGEntity label to each node."""
    rows = [
        {
            "eid":         rec.element_id,
            "description": rec.description,
            "embedding":   rec.embedding,
        }
        for rec in batch
    ]
    with driver.session() as s:
        s.run(
            """
            UNWIND $rows AS row
            MATCH (n) WHERE elementId(n) = row.eid
            SET n.embeddingDescription = row.description,
                n.embedding            = row.embedding,
                n:KGEntity
            """,
            rows=rows,
        )


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in resp.data]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Embed entity nodes for the Lewis & Clark KG.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sample descriptions without writing to Neo4j.")
    parser.add_argument("--reindex-only", action="store_true",
                        help="Create/recreate per-label vector indexes without re-embedding.")
    args = parser.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")

    if args.reindex_only:
        print("\nCreating per-label vector indexes ...")
        create_vector_indexes(driver)
        driver.close()
        print("\nDone.")
        return

    # ── Fetch ─────────────────────────────────────────────────────────────────
    print("Fetching standard entity nodes ...")
    entity_records = fetch_entities(driver)
    print(f"  {len(entity_records)} entities across {len(ENTITY_LABELS)} labels.")

    print("Fetching Taxon nodes with descendant aliases ...")
    taxon_records = fetch_taxons(driver)
    print(f"  {len(taxon_records)} Taxon nodes.")

    all_records = entity_records + taxon_records

    # ── Build descriptions ────────────────────────────────────────────────────
    print("Building descriptions ...")
    by_label: dict[str, int] = {}
    for rec in all_records:
        rec.description = (
            build_taxon_description(rec)
            if rec.label == "Taxon"
            else build_description(rec)
        )
        by_label[rec.label] = by_label.get(rec.label, 0) + 1

    for label, count in sorted(by_label.items()):
        print(f"  {label}: {count}")
    print(f"  Total: {len(all_records)}")

    if args.dry_run:
        import random
        print("\n── Sample entity descriptions ───────────────────────────────────")
        samples = random.sample(entity_records, min(10, len(entity_records)))
        samples.sort(key=lambda r: r.label)
        for rec in samples:
            print(f"\n[{rec.label}]  {rec.canonical_name}")
            print(f"  {rec.description}")

        print("\n── Sample Taxon descriptions ────────────────────────────────────")
        # Show one taxon per rank that has aliases
        shown: set[str] = set()
        for rec in taxon_records:
            if rec.taxon_rank not in shown and rec.aliases:
                print(f"\n[Taxon/{rec.taxon_rank}]  {rec.canonical_name}")
                print(f"  {rec.description}")
                shown.add(rec.taxon_rank)
        driver.close()
        return

    # ── Embed ─────────────────────────────────────────────────────────────────
    client = OpenAI(api_key=OPENAI_API_KEY)
    total = len(all_records)
    print(f"\nEmbedding {total} descriptions with {EMBEDDING_MODEL} ...")
    t0 = time.time()

    for i in range(0, total, EMBED_BATCH_SIZE):
        batch = all_records[i : i + EMBED_BATCH_SIZE]
        embeddings = embed_batch(client, [r.description for r in batch])
        for rec, emb in zip(batch, embeddings):
            rec.embedding = emb
        print(f"  {min(i + EMBED_BATCH_SIZE, total)}/{total} embedded")

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    # ── Write ─────────────────────────────────────────────────────────────────
    print(f"\nWriting to Neo4j in batches of {WRITE_BATCH_SIZE} ...")
    for i in range(0, total, WRITE_BATCH_SIZE):
        batch = all_records[i : i + WRITE_BATCH_SIZE]
        write_batch(driver, batch)
        print(f"  {min(i + WRITE_BATCH_SIZE, total)}/{total} written")

    # ── Vector indexes (one per label) ────────────────────────────────────────
    print("\nCreating per-label vector indexes ...")
    create_vector_indexes(driver)

    driver.close()
    print(f"\nDone. {total} nodes embedded with per-label vector indexes.")


if __name__ == "__main__":
    main()
