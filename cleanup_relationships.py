#!/usr/bin/env python3
"""
Lewis & Clark — relationship cleanup.

Uses apoc.refactor.invert to flip reversed relationships and a parameterised
delete to remove those that don't belong in either direction.

SCHEMA drives both passes — add or adjust entries here when the extraction
schema in extract.py changes.

Pass 1 — FLIP
  For each relationship type, find relationships where:
    • the source label is not a valid source          (wrong end)
    • the target label is not a valid target          (wrong end)
    • the target label IS a valid source              (belongs at the start)
    • the source label IS a valid target              (belongs at the end)
  apoc.refactor.invert swaps direction and copies all properties in one call.

Pass 2 — DELETE
  After flipping, delete any remaining relationship whose source label is not
  in allowedSources or whose target label is not in allowedTargets.
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# ── Schema ────────────────────────────────────────────────────────────────────
# Maps each relationship type to (allowed_source_labels, allowed_target_labels).
# Must stay in sync with REL_SCHEMA in extract.py.

SCHEMA: dict[str, tuple[set[str], set[str]]] = {
    "CAMPED_AT":       ({"Person"},                          {"Place", "WaterBody"}),
    "VISITED":         ({"Person"},                          {"Place", "WaterBody"}),
    "OBSERVED":        ({"Person"},                          {"AnimalSpecies", "PlantSpecies", "Place", "WaterBody"}),
    "MET_WITH":        ({"Person", "NativeNation"},          {"Person", "NativeNation"}),
    "TRADED_WITH":        ({"Person", "NativeNation"},          {"Person", "NativeNation"}),
    "ACQUIRED_PROVISION": ({"Person", "NativeNation"},          {"Supply"}),
    "GUIDED":          ({"Person"},                          {"Person"}),
    "MEMBER_OF":       ({"Person"},                          {"NativeNation"}),
    "NAMED":           ({"Person"},                          {"Place", "WaterBody"}),
    "INTERPRETED_FOR": ({"Person"},                          {"Person", "NativeNation"}),
    "ORIGINATED_FROM": ({"Person", "NativeNation"},          {"NativeNation", "Place", "WaterBody"}),
    "PARTICIPATED_IN":  ({"Person", "NativeNation"},          {"Event"}),
}

# ── Cypher templates ──────────────────────────────────────────────────────────

FLIP_CYPHER = """
MATCH (s)-[r:$($type)]->(t)
WHERE none(l IN $allowedSources WHERE l IN labels(s))
  AND none(l IN $allowedTargets WHERE l IN labels(t))
  AND any(l  IN $allowedSources WHERE l IN labels(t))
  AND any(l  IN $allowedTargets WHERE l IN labels(s))
CALL apoc.refactor.invert(r) YIELD input, output, error
RETURN count(input) AS flipped
"""

DELETE_CYPHER = """
MATCH (s)-[r:$($type)]->(t)
WHERE NOT any(l IN $allowedSources WHERE l IN labels(s))
   OR NOT any(l IN $allowedTargets WHERE l IN labels(t))
DELETE r
RETURN count(*) AS deleted
"""

# ── Runner ────────────────────────────────────────────────────────────────────

def run(session, cypher: str, params: dict) -> int:
    result = session.run(cypher, **params)
    record = result.single()
    return record[0] if record else 0


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.\n")

    total_flipped = 0
    total_deleted = 0

    with driver.session() as session:
        print("── Pass 1: Flip wrong-direction relationships ───────────────────────")
        for rel_type, (sources, targets) in SCHEMA.items():
            params = {
                "type":           rel_type,
                "allowedSources": list(sources),
                "allowedTargets": list(targets),
            }
            n = run(session, FLIP_CYPHER, params)
            if n:
                print(f"  {n:>5}  {rel_type}")
            total_flipped += n
        print(f"  {'─'*5}  total flipped: {total_flipped}\n")

        print("── Pass 2: Delete invalid source/target combinations ───────────────")
        for rel_type, (sources, targets) in SCHEMA.items():
            params = {
                "type":           rel_type,
                "allowedSources": list(sources),
                "allowedTargets": list(targets),
            }
            n = run(session, DELETE_CYPHER, params)
            if n:
                print(f"  {n:>5}  {rel_type}")
            total_deleted += n
        print(f"  {'─'*5}  total deleted: {total_deleted}\n")

    driver.close()
    print("Done.")


if __name__ == "__main__":
    main()
