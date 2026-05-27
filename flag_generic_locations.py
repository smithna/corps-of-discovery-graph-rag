#!/usr/bin/env python3
"""
Flag generic Place and WaterBody nodes with an additional :GenericLocation label.

Two passes:
  1. Blocklist — instant, free. Catches clear-cut generic nouns.
  2. LLM — classifies anything not caught by the blocklist, handling archaic
     spellings (Broock, Vally, Incampments) and mis-extractions (Boat, Canoes).

Nodes like CAMP, ISLAND, VILLAGE are common nouns extracted without enough
context to identify a specific location. Adding :GenericLocation lets queries
filter them out without removing the nodes (they may still carry useful
MENTIONED_IN or CAMPED_AT relationships).

Usage:
    python3 flag_generic_locations.py

To exclude generic locations in a query:
    MATCH (p:Place) WHERE NOT p:GenericLocation ...
"""

import json
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
LLM_MODEL      = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")

LLM_BATCH_SIZE = 60   # names per LLM call

# ── Pass 1: blocklist ─────────────────────────────────────────────────────────

GENERIC_TERMS = {
    # Navigational side-of-river references — never a place
    "S. S.", "L. S.", "L. S", "S. S", "LARD", "STARD", "STBD",
    "LARD SIDE", "STARD SIDE", "LARD. SIDE", "STARD. SIDE",
    "LARD SHORE", "STARD SHORE", "LARD. SHORE", "STARD. SHORE",
    "LARBORD SHORE", "LARBORD SIDE",
    "L. SIDE", "L. SIDE.", "STAR SIDE", "STD SIDE", "STARD. SADE",
    "LEFT SIDE", "RIGHT SIDE",
    "N. SIDE", "S. SIDE", "E. SIDE", "W. SIDE",
    "N. E. SIDE", "S. E. SIDE", "N. W. SIDE", "S. W. SIDE",
    "N. E.", "S. E.", "N. W.", "S. W.",
    "N. BANK", "S. BANK", "E. BANK", "W. BANK",
    "ST. SIDE", "STARBD. SIDE", "LARD BEND",
    # Landforms
    "BANK", "BANKS", "BASIN", "BASON", "BAY", "BEACH", "BEND", "BLUFF",
    "BLUFFS", "BOG", "BOTTOM", "BOTTOMS", "BROOK", "CANYON", "CAPE", "CAVE",
    "CATARACT", "CHANNEL", "CLIFF", "CLIFFS", "COAST", "COVE", "CREEK",
    "CREEKS", "DIVIDE", "FALLS", "FLAT", "FLATS", "FORD", "FORK", "FORKS",
    "GAP", "GROVE", "GULF", "HARBOR", "HILL", "HILLS", "HOLLOW", "INLET",
    "ISLAND", "ISLANDS", "ISLET", "ISTHMUS", "LAKE", "LAKES", "LANDING",
    "LEDGE", "MARSH", "MEADOW", "MOUTH", "MOUND", "MOUNTAIN", "MOUNTAINS",
    "NARROWS", "OCEAN", "PASS", "PLAIN", "PLAINS", "PLATEAU", "POINT",
    "POND", "POOL", "PRAIRIE", "PRARIE", "PRARIES", "PRAIRIES", "RAPID",
    "RAPIDS", "RAVINE", "REEF", "RIDGE", "RIVER", "ROCK", "ROCKS", "SHOAL",
    "SHORE", "SLOUGH", "SOUND", "SPRING", "SPRINGS", "STRAIT", "STREAM",
    "SWAMP", "TIMBER", "VALLEY", "VALLIE", "VALLIES", "VALLY", "VALLEYS",
    # Human geography — too vague
    "BOAT", "CAMP", "CAMPMENT", "CAMPS", "CANOE", "CANOES", "COUNTREY",
    "COUNTRY", "ENCAMPMENT", "FISHERY", "FORT", "FORTIFICATION", "FORTS",
    "INCAMPMENT", "INCAMPMENTS", "LODGE", "LODGES", "SETTLEMENT", "TOWN",
    "VILLAGE", "VILLAGES",
}


def apply_blocklist(driver) -> int:
    with driver.session() as s:
        result = s.run(
            "MATCH (p:Place|WaterBody) WHERE p.canonicalName IN $terms "
            "SET p:GenericLocation RETURN count(*) AS n",
            terms=list(GENERIC_TERMS),
        )
        return result.single()["n"]


# ── Pass 1b: proper-names safelist ────────────────────────────────────────────
# These nodes must never be flagged as generic regardless of their appearance.
# Add entries here whenever the LLM or blocklist incorrectly flags a known
# specific location.

PROPER_NAMES: set[str] = {
    # Mountain ranges and peaks
    "ROCKY MOUNTAINS", "ROCKY MTS.", "GATES OF THE ROCKY MOUNTAINS",
    "MT. HOODS", "MOUNT HOOD", "MT. ST. HILIANS", "MOUNT ST. HELENS",
    "BITTERROOT MOUNTAINS", "BITTERROOT RANGE",
    # Major rivers
    "MISSOURI RIVER", "COLUMBIA RIVER", "YELLOWSTONE RIVER",
    "JEFFERSON RIVER", "MADISON RIVER", "GALLATIN RIVER",
    "MARIA'S RIVER", "MARIAS RIVER", "LEWIS RIVER", "SNAKE RIVER",
    "PLATTE RIVER", "KANSAS RIVER", "OSAGE RIVER", "KOOSKOOSKE RIVER",
    "WILLAMETTE RIVER", "MULTNOMAH RIVER", "MULTNOMAR RIVER",
    "QUICKSAND RIVER", "QUICK SAND RIVER", "SANDY RIVER",
    "KNIFE RIVER", "HEART RIVER", "LITTLE MISSOURI RIVER",
    # Archaic / phonetic spellings of the Yellowstone River
    "ROCHEJHONE", "ROCHE JOHNE", "ROKEJHONE", "REJHONE", "ROLOJE",
    # Named confluences and landmarks
    "THREE FORKS OF THE MISSOURI", "GREAT FALLS OF THE MISSOURI",
    "MERIWETHER'S BAY",
    # Named tributaries (Philosophy, Wisdom, Philanthropy — Jefferson named them)
    "PHILANTHROPY RIVER", "PHILANTHROPHY", "PHILANTHOPHY",
    "WISDOM RIVER", "WISDOM",
    # Pacific
    "PACIFIC OCEAN", "GREAT WESTERN OCEAN",
}


def apply_proper_safelist(driver) -> int:
    """Remove :GenericLocation from any node whose canonicalName is in PROPER_NAMES,
    and ensure they are never re-flagged by subsequent LLM passes."""
    with driver.session() as s:
        result = s.run(
            "MATCH (p:Place|WaterBody) WHERE p.canonicalName IN $names "
            "AND p:GenericLocation "
            "REMOVE p:GenericLocation RETURN count(*) AS n",
            names=list(PROPER_NAMES),
        )
        return result.single()["n"]


# ── Pass 2: LLM classification ────────────────────────────────────────────────

_LLM_SYSTEM = """
You are helping clean a knowledge graph extracted from the Lewis & Clark
Expedition journals (1804-1806).

You will receive a list of Place or WaterBody node names. Classify each as:
  "generic"  — a bare geographic type word, or one qualified only by size,
               direction, or vague descriptors, with no specific identifying
               name. These could refer to any of hundreds of unnamed features.
               Examples: RIVER, CREEK, ISLAND, CAMP, VILLAGE, BOTTOM,
               SMALL CREEK, LARGE RIVER, NORTH FORK, ANOTHER LARGE RIVER,
               S. S., LARD SIDE, LEFT SIDE, OPPOSITE BANK
  "proper"   — a specific named location with an identifying proper name,
               even if the spelling is archaic, phonetic, or abbreviated.
               Examples: FORT MANDAN, MISSOURI RIVER, PACIFIC OCEAN,
               JEFFERSON RIVER, GALLATIN RIVER, GREAT FALLS OF THE MISSOURI,
               MARIA'S RIVER, GATES OF THE ROCKY MOUNTAINS, ROCKY MOUNTAINS,
               HUNGRY CREEK, ROCHEJHONE (Yellowstone River),
               MULTNOMAR RIVER (Willamette), PHILANTHROPHY (Philosophy River)

Rules:
- A name is PROPER if it contains a specific identifier that distinguishes it
  from all other features of the same type. JEFFERSON RIVER is not just any
  river; FORT MANDAN is not just any fort.
- A name is GENERIC if it has no specific identifier — LARGE RIVER, SMALL
  CREEK, NORTH FORK, ANOTHER LARGE RIVER could describe anywhere.
- Named mountain ranges, rivers, creeks, oceans, falls, and landmarks are
  PROPER even when their name contains a generic geographic word.
- Archaic or phonetic spellings of known places are PROPER.
- When in doubt, classify as "proper".

Respond with a JSON object mapping each name exactly as given to "generic" or
"proper". No explanation, no markdown — just the JSON object.
""".strip()


def classify_with_llm(client: OpenAI, names: list[str]) -> dict[str, str]:
    """Return {name: 'generic'|'proper'} for each name in the batch."""
    user_content = json.dumps(names)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"  LLM error: {exc}")
        return {}


def apply_llm_pass(driver, client: OpenAI) -> int:
    """Classify untagged nodes with the LLM and flag the generic ones."""
    with driver.session() as s:
        rows = s.run(
            "MATCH (p:Place|WaterBody) WHERE NOT p:GenericLocation "
            "RETURN p.canonicalName AS cn ORDER BY cn"
        ).data()

    names = [r["cn"] for r in rows]
    if not names:
        return 0

    flagged = 0
    for i in range(0, len(names), LLM_BATCH_SIZE):
        batch = names[i : i + LLM_BATCH_SIZE]
        classifications = classify_with_llm(client, batch)
        generic = [n for n, label in classifications.items() if label == "generic"]
        if generic:
            with driver.session() as s:
                s.run(
                    "UNWIND $names AS cn "
                    "MATCH (p:Place|WaterBody {canonicalName: cn}) "
                    "SET p:GenericLocation",
                    names=generic,
                )
            flagged += len(generic)
        print(f"  Batch {i // LLM_BATCH_SIZE + 1}: "
              f"{len(batch)} classified, {len(generic)} generic")

    return flagged


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("Connected to Neo4j.")

    n1 = apply_blocklist(driver)
    print(f"Pass 1 (blocklist):      {n1} nodes flagged.")

    n2 = apply_llm_pass(driver, client)
    print(f"Pass 2 (LLM):            {n2} nodes flagged.")

    n3 = apply_proper_safelist(driver)
    print(f"Pass 3 (proper safelist): {n3} nodes un-flagged.")

    driver.close()
    print(f"\nDone. {n1 + n2 - n3} net nodes flagged as :GenericLocation.")


if __name__ == "__main__":
    main()
