#!/usr/bin/env python3
"""
Lewis & Clark Journals — full-text index setup.

Creates a full-text index per entity label covering canonicalName, name, and
aliases so that queries can resolve variant spellings and historical aliases
without needing exact canonicalName matches.

Index naming convention: <label_snake_case>_search
  e.g. db.index.fulltext.queryNodes('native_nation_search', 'Minnetarees')
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

INDEXES = [
    ("Person",        "person_search"),
    ("NativeNation",  "native_nation_search"),
    ("Place",         "place_search"),
    ("WaterBody",     "waterbody_search"),
    ("PlantSpecies",  "plant_species_search"),
    ("AnimalSpecies", "animal_species_search"),
    ("Supply",        "supply_search"),
    ("Event",         "event_search"),
]

ALL_LABELS = [label for label, _ in INDEXES]


def create_indexes(driver) -> None:
    with driver.session() as s:
        # Per-label indexes for precise typed queries
        for label, index_name in INDEXES:
            s.run(f"""
                CREATE FULLTEXT INDEX {index_name} IF NOT EXISTS
                FOR (n:{label})
                ON EACH [n.canonicalName, n.name, n.aliases]
            """)
            print(f"  {index_name}  ({label})")

        # Combined geographic index — Place and WaterBody share one index so
        # queries resolve correctly regardless of which label the LLM declares
        # (e.g. a query for "Fort Mandan" works whether label is Place or WaterBody)
        s.run("""
            CREATE FULLTEXT INDEX location_search IF NOT EXISTS
            FOR (n:Place|WaterBody)
            ON EACH [n.canonicalName, n.name, n.aliases]
        """)
        print(f"  location_search  (Place|WaterBody)")

        # Cross-label index for exploratory searches where the entity type
        # is unknown (e.g. 'Continental Divide' could be Place or Event)
        labels_union = "|".join(ALL_LABELS)
        s.run(f"""
            CREATE FULLTEXT INDEX entity_search IF NOT EXISTS
            FOR (n:{labels_union})
            ON EACH [n.canonicalName, n.name, n.aliases]
        """)
        print(f"  entity_search  (all labels)")


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.\nCreating full-text indexes:")
    create_indexes(driver)
    driver.close()
    print("Done.")


if __name__ == "__main__":
    main()
