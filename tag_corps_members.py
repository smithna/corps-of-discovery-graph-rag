#!/usr/bin/env python3
"""
Lewis & Clark Journals — tag Corps of Discovery members.

Sets corpsMember=true on every Person node whose canonicalName belongs to the
permanent party or attached personnel of the Corps of Discovery.  All other
Person nodes are left without the property (treated as false/absent).

This flag lets Cypher queries distinguish expedition acquisitions from those
of Native individuals:
  WHERE p.corpsMember = true   →  expedition member
  WHERE NOT p.corpsMember      →  Native American, trader, or unknown
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# Canonical names of every person who travelled with the Corps of Discovery,
# including the permanent party, interpreters, and attached personnel.
CORPS_MEMBERS: frozenset[str] = frozenset({
    # Commanding officers
    "MERIWETHER LEWIS",
    "WILLIAM CLARK",

    # Sergeants
    "JOHN ORDWAY",
    "PATRICK GASS",
    "NATHANIEL PRYOR",
    "CHARLES FLOYD",          # died August 1804

    # Privates — permanent party
    "JOSEPH FIELD",
    "REUBIN FIELD",
    "GEORGE SHANNON",
    "JOHN COLTER",
    "WILLIAM BRATTON",
    "JOHN COLLINS",
    "PIERRE CRUZATTE",
    "ROBERT FRAZER",
    "SILAS GOODRICH",
    "HUGH HALL",
    "THOMAS HOWARD",
    "FRANCOIS LABICHE",
    "JEAN BAPTISTE LEPAGE",   # joined at Fort Mandan
    "HUGH MCNEAL",
    "JOHN POTTS",
    "GEORGE GIBSON",
    "JOHN SHIELDS",
    "JOHN B. THOMPSON",
    "PETER WEISER",
    "JOSEPH WHITEHOUSE",
    "ALEXANDER WILLARD",
    "RICHARD WINDSOR",
    "WERNER",                 # William Werner
    "HOWARD",                 # Thomas P. Howard

    # Civilian hunter / interpreter
    "GEORGE DROUILLARD",

    # Clark's enslaved companion
    "YORK",

    # Interpreter and family
    "TOUSSAINT CHARBONNEAU",
    "SACAGAWEA",
    "JEAN BAPTISTE CHARBONNEAU",  # Pomp, born during expedition

    # Members who returned early or were discharged
    "JOHN NEWMAN",            # discharged for mutinous expression
    "MOSES REED",             # deserted, returned
    "JOHN DAME",
    "EBENEZER TUTTLE",
    "ISAAC WHITE",

    # Corporal
    "RICHARD WARFINGTON",     # led the return keelboat party
})


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as s:
        # Set corpsMember=true on known members
        result = s.run(
            """
            UNWIND $names AS name
            MATCH (p:Person {canonicalName: name})
            SET p.corpsMember = true
            RETURN count(*) AS tagged
            """,
            names=list(CORPS_MEMBERS),
        )
        tagged = result.single()["tagged"]

        # Clear the flag from any node that shouldn't have it
        # (guards against stale data from a previous run with a wider list)
        result = s.run(
            """
            MATCH (p:Person)
            WHERE p.corpsMember = true AND NOT p.canonicalName IN $names
            REMOVE p.corpsMember
            RETURN count(*) AS cleared
            """,
            names=list(CORPS_MEMBERS),
        )
        cleared = result.single()["cleared"]

        # Report which known members were not found in the graph
        result = s.run(
            """
            UNWIND $names AS name
            OPTIONAL MATCH (p:Person {canonicalName: name})
            WITH name, p
            WHERE p IS NULL
            RETURN name
            ORDER BY name
            """,
            names=list(CORPS_MEMBERS),
        )
        missing = [r["name"] for r in result]

    driver.close()

    print(f"  Tagged {tagged} Corps members with corpsMember=true")
    if cleared:
        print(f"  Cleared stale corpsMember flag from {cleared} nodes")
    if missing:
        print(f"  Not found in graph ({len(missing)}): {', '.join(missing)}")
    else:
        print("  All known Corps members found in graph.")


if __name__ == "__main__":
    main()
