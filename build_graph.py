#!/usr/bin/env python3
"""
Lewis & Clark Knowledge Graph — full build pipeline.

Steps:
  1.  Clear the graph (entity nodes + graphExtracted flags, or everything)
  2.  Ingest corpus (fetch, chunk, embed, load Chunk nodes)
  3.  Extract entities and relationships
  4.  Resolve single-word Person mentions
  5.  Fix WaterBody labels
  6.  Flag generic locations
  7.  Enrich Sacagawea references
  8.  Enrich species with GBIF taxonomy
  9.  Clean up relationships (flip reversed; delete invalid source/target combos)
  10. Disambiguate — pass 1 (identify + merge)
  11. Disambiguate — pass 2 (catch stragglers after first merge)
  12. Create full-text indexes
  13. Embed entity nodes

Flags:
  --skip-ingest   Keep existing Chunk nodes; only clear entity nodes and
                  re-run steps 3-8. Saves embedding API cost when you only
                  need to improve extraction or disambiguation.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

HERE = Path(__file__).parent

# ── Helpers ───────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def run(script: str, *args: str) -> None:
    """Run a pipeline script as a subprocess; abort on failure."""
    cmd = [sys.executable, str(HERE / script), *args]
    t0 = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"\nABORTED: {script} exited with code {result.returncode}.")
        sys.exit(result.returncode)
    print(f"  ✓ {script} completed in {elapsed:.0f}s")


# ── Graph clearing ────────────────────────────────────────────────────────────

def clear_entities(driver) -> None:
    """Delete all non-Chunk nodes and reset graphExtracted on Chunks."""
    print("  Deleting entity nodes ...")
    with driver.session() as s:
        s.run("""
            MATCH (n) WHERE NOT n:Chunk
            CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS
        """)
        s.run("""
            MATCH (c:Chunk)
            CALL { WITH c REMOVE c.graphExtracted } IN TRANSACTIONS OF 1000 ROWS
        """)
    print("  Entity nodes deleted; graphExtracted flags reset.")


def clear_all(driver) -> None:
    """Delete every node and relationship in the database."""
    print("  Deleting all nodes ...")
    with driver.session() as s:
        s.run("""
            MATCH (n)
            CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS
        """)
    print("  Database cleared.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Lewis & Clark knowledge graph end-to-end."
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Keep existing Chunk nodes; re-run only extraction onwards.",
    )
    args = parser.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")

    t_start = time.time()

    # ── Step 1: clear ─────────────────────────────────────────────────────────
    banner("Step 1 — Clear graph")
    if args.skip_ingest:
        clear_entities(driver)
    else:
        clear_all(driver)

    driver.close()

    # ── Step 2: ingest ────────────────────────────────────────────────────────
    if not args.skip_ingest:
        banner("Step 2 — Ingest corpus (fetch · chunk · embed · load)")
        run("ingest.py")
    else:
        print("\nStep 2 — Ingest skipped (--skip-ingest)")

    # ── Step 3: extract ───────────────────────────────────────────────────────
    banner("Step 3 — Extract entities and relationships")
    run("extract.py")

    # ── Step 4: resolve mentions ──────────────────────────────────────────────
    banner("Step 4 — Resolve single-word Person mentions")
    run("resolve_mentions.py")

    # ── Step 5: fix WaterBody labels ──────────────────────────────────────────
    banner("Step 5 — Relabel Place → WaterBody where appropriate")
    run("fix_waterbody_labels.py")

    # ── Step 6: flag generic locations ───────────────────────────────────────
    banner("Step 6 — Flag generic Place/WaterBody nodes")
    run("flag_generic_locations.py")

    # ── Step 7: Sacagawea enrichment ──────────────────────────────────────────
    # Creates Sacagawea's Person node (she is rarely named directly) and links
    # it to chunks identified by the curated lewis-clark.org annotation list,
    # matching by date then string similarity.
    banner("Step 7 — Enrich Sacagawea references")
    run("enrich_sacagawea.py")

    # ── Step 8: taxonomy enrichment ───────────────────────────────────────────
    # Runs before disambiguation so that common-name → scientific-name renames
    # and merges are visible to the co-occurrence graph and string signals.
    banner("Step 8 — Enrich species with GBIF taxonomy")
    run("add_taxonomy.py")

    # ── Step 9: clean up relationships ───────────────────────────────────────
    # Flip reversed relationships and delete invalid source/target combos before
    # disambiguation so its co-occurrence signals aren't polluted by bad edges.
    banner("Step 9 — Clean up relationships (flip reversed; delete invalid)")
    run("cleanup_relationships.py")

    # ── Steps 10-13: disambiguate (two passes) ────────────────────────────────
    banner("Step 10 — Disambiguate pass 1: identify duplicates")
    run("disambiguate.py", "--phase", "1")

    banner("Step 11 — Disambiguate pass 1: merge components")
    run("disambiguate.py", "--phase", "2")

    banner("Step 12 — Disambiguate pass 2: identify stragglers")
    run("disambiguate.py", "--phase", "1")

    banner("Step 13 — Disambiguate pass 2: merge stragglers")
    run("disambiguate.py", "--phase", "2")

    # ── Step 14: tag Corps members ────────────────────────────────────────────
    banner("Step 14 — Tag Corps of Discovery members (corpsMember=true)")
    run("tag_corps_members.py")

    # ── Step 15: full-text indexes ────────────────────────────────────────────
    banner("Step 15 — Create full-text indexes")
    run("setup_fulltext_indexes.py")

    # ── Step 16: entity embeddings ────────────────────────────────────────────
    banner("Step 16 — Embed entity nodes (per-label vector indexes)")
    run("embed_entities.py")

    # ── Done ──────────────────────────────────────────────────────────────────
    total = time.time() - t_start
    banner(f"Build complete — {total / 60:.1f} min total")


if __name__ == "__main__":
    main()
