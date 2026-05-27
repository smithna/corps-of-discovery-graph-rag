#!/usr/bin/env python3
"""
One-time migration: reclassify Place nodes whose names contain water-related
terms (river, creek, fork, rapids, falls, lake, bay, strait, sound, inlet,
pond, spring, brook, stream) to WaterBody.

Two cases:
  1. No WaterBody with the same name exists → remove Place label, add WaterBody.
  2. A WaterBody with the same name already exists → merge the Place into it
     with APOC, preserving all relationships and aliases.
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

WATER_TERMS = [
    "river", "creek", "fork", "rapids", "falls", "lake", "bay",
    "strait", "sound", "inlet", "pond", "spring", "brook", "stream",
]

def relabel_no_conflict(driver) -> int:
    """Swap label for Place nodes with no existing WaterBody counterpart."""
    result = driver.execute_query("""
        MATCH (p:Place)
        WHERE ANY(wt IN $water_terms WHERE toLower(p.canonicalName) CONTAINS wt)
          AND NOT EXISTS { MATCH (:WaterBody {canonicalName: p.canonicalName}) }
        REMOVE p:Place
        SET p:WaterBody
        RETURN count(*) AS n
    """, water_terms=WATER_TERMS)
    return result.records[0]["n"]


def merge_conflicts(driver) -> int:
    """For Place nodes that share a canonicalName with an existing WaterBody, merge them."""
    # Collect conflicting pairs first (can't iterate + mutate in one pass)
    pairs = driver.execute_query("""
        MATCH (p:Place), (w:WaterBody)
        WHERE ANY(wt IN $water_terms WHERE toLower(p.canonicalName) CONTAINS wt)
          AND p.canonicalName = w.canonicalName
        RETURN elementId(p) AS place_id, elementId(w) AS water_id
    """, water_terms=WATER_TERMS).records

    if not pairs:
        return 0

    merged = 0
    for rec in pairs:
        driver.execute_query("""
            MATCH (w:WaterBody) WHERE elementId(w) = $wid
            MATCH (p:Place)     WHERE elementId(p) = $pid
            WITH collect(w) + collect(p) AS nodes,
                 reduce(acc = [], x IN collect(w) + collect(p) |
                     acc + coalesce(x.aliases, []) + [x.name]
                 ) AS rawAliases,
                 w.canonicalName AS cn
            CALL apoc.refactor.mergeNodes(nodes, {
                mergeRels: false,
                produceSelfRel: false
            })
            YIELD node
            REMOVE node:Place
            SET node.canonicalName = cn,
                node.name          = apoc.text.capitalizeAll(toLower(cn)),
                node.aliases       = [x IN apoc.coll.toSet(rawAliases) WHERE x <> apoc.text.capitalizeAll(toLower(cn))]
        """, wid=rec["water_id"], pid=rec["place_id"])
        merged += 1

    return merged


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")

    relabeled = relabel_no_conflict(driver)
    print(f"Relabeled {relabeled} Place → WaterBody (no conflict).")

    merged = merge_conflicts(driver)
    print(f"Merged    {merged} Place nodes into existing WaterBody nodes.")

    driver.close()
    print("Done.")


if __name__ == "__main__":
    main()
