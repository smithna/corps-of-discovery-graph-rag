#!/usr/bin/env python3
"""
Lewis & Clark Journals — single-word Person mention resolution.

For each (single-word Person, Chunk) pair:
  1. Finds candidate full-name Person nodes in neighboring chunks whose name
     contains the short name as a substring.
  2. Passes the candidate list and the chunk text to GPT-4o-mini.
  3. If the LLM resolves it, re-routes the MENTIONED_IN relationship from the
     ambiguous node to the correct full-name node.

After processing, single-word Person nodes with no remaining MENTIONED_IN
relationships are deleted — they have been fully absorbed into full-name nodes.

Run this before disambiguate.py.
"""

import asyncio
import os
from pydantic import BaseModel

from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import AsyncOpenAI

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

NEO4J_URI        = os.environ["NEO4J_URI"]
NEO4J_USER       = os.environ["NEO4J_USER"]
NEO4J_PASSWORD   = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
RESOLUTION_MODEL = os.getenv("RESOLUTION_MODEL", "gpt-4o-mini")
CONCURRENCY      = int(os.getenv("EXTRACTION_CONCURRENCY", "5"))
NEIGHBOR_HOPS    = 3   # NEXT_CHUNK hops to search for candidate full names

# ── Structured output ─────────────────────────────────────────────────────────

class MentionResolution(BaseModel):
    resolved: bool
    full_name: str  # exact name from the candidate list; ignored if resolved=False

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an expert on the Lewis and Clark Expedition (1804-1806).

You will be given a passage from the expedition journals, a short name that
appears in or near that passage, and a list of candidate full names.

Determine which candidate the short name refers to in this specific passage.
Use the passage text as your primary evidence.

If you can identify the person with confidence, set resolved=True and provide
their full name exactly as it appears in the candidate list.
If the passage does not provide enough context to distinguish between candidates,
set resolved=False.
""".strip()

# ── Data loading ──────────────────────────────────────────────────────────────

# Single-name Person nodes that are unambiguously one individual and need no
# resolution — skip them to avoid pointless LLM calls.
_SKIP_RESOLUTION = {"YORK", "SCANNON"}

def load_mentions(driver) -> list[dict]:
    with driver.session() as s:
        rows = s.run("""
            MATCH (p:Person)-[:MENTIONED_IN]->(c:Chunk)
            WHERE NOT p.canonicalName CONTAINS ' '
            RETURN p.canonicalName AS canonicalName,
                   p.name         AS name,
                   c.chunkId      AS chunkId,
                   c.text         AS text
            ORDER BY c.sequence
        """).data()
    return [r for r in rows if r["canonicalName"] not in _SKIP_RESOLUTION]


def get_candidates(driver, name: str, chunk_id: str) -> list[dict]:
    """Return [{displayName, canonicalName}] for full-name Persons in nearby chunks."""
    with driver.session() as s:
        rows = s.run(f"""
            MATCH (c:Chunk {{chunkId: $chunkId}})
            MATCH (c)-[:NEXT_CHUNK*0..{NEIGHBOR_HOPS}]-(nearby:Chunk)<-[:MENTIONED_IN]-(fp:Person)
            WHERE fp.canonicalName CONTAINS ' '
              AND ANY(n IN fp.aliases + [fp.name, fp.canonicalName] WHERE toUpper(n) CONTAINS toUpper($name))
            RETURN DISTINCT fp.name AS displayName, fp.canonicalName AS canonicalName
        """, chunkId=chunk_id, name=name).data()
    return rows

# ── Resolution ────────────────────────────────────────────────────────────────

async def resolve_mention(
    client: AsyncOpenAI,
    driver,
    canonical_name: str,
    display_name: str,
    chunk_id: str,
    text: str,
    semaphore: asyncio.Semaphore,
    idx: int,
    total: int,
) -> None:
    async with semaphore:
        candidates = get_candidates(driver, display_name, chunk_id)
        if not candidates:
            return

        display_names = [c["displayName"] for c in candidates]
        cn_by_display = {c["displayName"]: c["canonicalName"] for c in candidates}

        try:
            resp = await client.beta.chat.completions.parse(
                model=RESOLUTION_MODEL,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": (
                        f"Short name: {display_name}\n"
                        f"Candidates: {', '.join(display_names)}\n\n"
                        f"Passage:\n{text}"
                    )},
                ],
                response_format=MentionResolution,
            )
            result = resp.choices[0].message.parsed
            if not result or not result.resolved:
                return

            if result.full_name not in cn_by_display:
                print(f"  [{idx:>4}/{total}] INVALID  '{result.full_name}' not in candidates")
                return

            full_canonical = cn_by_display[result.full_name]
            with driver.session() as s:
                s.run("""
                    MATCH (p:Person {canonicalName: $cn})-[r:MENTIONED_IN]->(c:Chunk {chunkId: $chunkId})
                    MATCH (fp:Person {canonicalName: $fullCn})
                    MERGE (fp)-[:MENTIONED_IN]->(c)
                    DELETE r
                """, cn=canonical_name, chunkId=chunk_id, fullCn=full_canonical)

            print(f"  [{idx:>4}/{total}] '{display_name}'  →  '{result.full_name}'")

        except Exception as e:
            print(f"  [{idx:>4}/{total}] ERROR  {name} / {chunk_id}: {e}")


def cleanup_empty_nodes(driver) -> None:
    with driver.session() as s:
        result = s.run("""
            MATCH (p:Person)
            WHERE NOT p.canonicalName CONTAINS ' '
              AND NOT (p)-[:MENTIONED_IN]->()
            DETACH DELETE p
            RETURN count(*) AS deleted
        """)
        deleted = result.single()["deleted"]
    print(f"Deleted {deleted} fully-resolved single-word Person nodes.")

# ── Main ──────────────────────────────────────────────────────────────────────

async def run(driver) -> None:
    client  = AsyncOpenAI(api_key=OPENAI_API_KEY)
    mentions = load_mentions(driver)
    total    = len(mentions)
    print(f"Resolving {total} single-word Person mentions  "
          f"model={RESOLUTION_MODEL}  concurrency={CONCURRENCY}")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*[
        resolve_mention(
            client, driver,
            m["canonicalName"], m["name"], m["chunkId"], m["text"],
            semaphore, i + 1, total,
        )
        for i, m in enumerate(mentions)
    ])
    cleanup_empty_nodes(driver)


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")
    asyncio.run(run(driver))
    driver.close()
    print("\nMention resolution complete.")


if __name__ == "__main__":
    main()
