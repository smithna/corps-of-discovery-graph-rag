#!/usr/bin/env python3
"""
Lewis & Clark Journals — species taxonomy enrichment.

For each Species node, looks up the full taxonomic hierarchy from the GBIF
species match API and writes it to the graph as Taxon nodes linked by
BELONGS_TO relationships.

Graph structure added:
  (s:Species)-[:BELONGS_TO]->(t:Taxon {rank:'genus'})
                             -[:BELONGS_TO]->(t:Taxon {rank:'family'})
                             -[:BELONGS_TO]->(t:Taxon {rank:'order'})
                             -[:BELONGS_TO]->(t:Taxon {rank:'class'})
                             -[:BELONGS_TO]->(t:Taxon {rank:'phylum'})
                             -[:BELONGS_TO]->(t:Taxon {rank:'kingdom'})

Example query enabled by this:
  MATCH (p:Person)-[r:OBSERVED]->(s:AnimalSpecies)-[:BELONGS_TO*]->(t:Taxon {rank:'class', name:'Aves'})
  WHERE r.date > date('1805-08-12')
  RETURN s.name AS species, count(*) AS observations
  ORDER BY observations DESC

GBIF match API: https://api.gbif.org/v1/species/match  (free, no key required)

Handling:
  - Full binomials (CANIS LUPUS)  → queried as title-case species name
  - Genus placeholders (SALIX SP.) → queried at genus rank; linked to genus Taxon
  - Common-name canonicals (GROUSE, BERRIES) → GBIF match confidence too low; skipped
"""

import os
import time

import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
MIN_CONFIDENCE = 80     # below this we consider the match unreliable
REQUEST_DELAY  = 0.25   # seconds between GBIF calls
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
LLM_MODEL      = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")

# Ordered from broadest to most specific
RANKS = ["kingdom", "phylum", "class", "order", "family", "genus"]


# ── GBIF lookup ───────────────────────────────────────────────────────────────

def _is_sp(canonical: str) -> bool:
    """True for genus-level placeholders like 'SALIX SP.' or 'CANIS SP'."""
    upper = canonical.upper()
    return upper.endswith(" SP.") or upper.endswith(" SP")


LABEL_KINGDOM = {
    "PlantSpecies":  "Plantae",
    "AnimalSpecies": "Animalia",
}


def _build_params(canonical: str, kingdom: str | None = None, genus_only: bool = False) -> dict:
    """
    Build GBIF match query parameters.

    genus_only=False (default): pass genus + species for full-binomial precision.
    genus_only=True: pass only genus (used for the genus-fallback pass).

    kingdom constrains the search to Plantae or Animalia so that common
    names like "Flax" don't match same-named animal genera.

    If the first word is not a real genus (e.g. "BLACK" from "BLACK DUCK"),
    GBIF returns matchType=NONE and we fall through to the LLM fallback.
    """
    base: dict = {"strict": "false"}
    if kingdom:
        base["kingdom"] = kingdom

    # Strip SP. suffix before splitting
    clean = canonical.strip().upper().removesuffix(" SP.").removesuffix(" SP").strip()
    parts = clean.split()
    if parts:
        base["genus"] = parts[0].capitalize()
    if not genus_only and len(parts) >= 2:
        base["species"] = parts[1].lower()
    return base


def query_gbif(canonical: str, kingdom: str | None = None, genus_only: bool = False) -> dict | None:
    """
    Call the GBIF species match endpoint.  Returns the response dict if the
    match confidence is acceptable, otherwise None.
    """
    params = _build_params(canonical, kingdom, genus_only=genus_only)
    try:
        resp = requests.get(GBIF_MATCH_URL, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    HTTP error: {exc}")
        return None

    data = resp.json()
    if data.get("matchType") == "NONE":
        return None
    if data.get("confidence", 0) < MIN_CONFIDENCE:
        return None
    # Reject kingdom-only matches — we already know plant vs animal from the label
    if not any(data.get(r) for r in ("phylum", "class", "order", "family", "genus")):
        return None
    return data


# ── LLM common-name fallback ─────────────────────────────────────────────────

_LLM_SYSTEM = """
You are a naturalist specialising in the flora and fauna of North America
encountered during the Lewis and Clark Expedition (1804-1806).

You will be given a species name and the raw text forms (aliases) used for it
in the expedition journals. Use the geographic and temporal context of the
expedition: the Great Plains, Rocky Mountains, and Pacific Northwest in the
early 19th century.

Reply with one of:
  • A scientific binomial if you can identify the species (e.g. "Anas rubripes")
  • A genus name only if you can identify the genus but not the species (e.g. "Quercus")
  • The word: unknown  — if you cannot identify even the genus

No explanation, no punctuation, no authority — just the name or "unknown".
""".strip()


def fetch_sample_chunk(driver, canonical: str) -> str | None:
    """Return the text of one chunk that mentions this species, or None."""
    with driver.session() as s:
        rec = s.run(
            "MATCH (n:Species {canonicalName: $cn})-[:MENTIONED_IN]->(c:Chunk)"
            " RETURN c.text AS text LIMIT 1",
            cn=canonical,
        ).single()
    return rec["text"] if rec else None


def llm_scientific_name(
    client: OpenAI,
    common_name: str,
    aliases: list[str],
    kingdom: str | None = None,
    sample_chunk: str | None = None,
) -> str | None:
    """Ask the LLM for a full scientific binomial, providing aliases as context."""
    KINGDOM_CONSTRAINT = {
        "Animalia": "IMPORTANT: this is an animal species — your answer must be an animal, not a plant or fungus.",
        "Plantae":  "IMPORTANT: this is a plant species — your answer must be a plant, not an animal or fungus.",
    }
    parts = [f"Name: {common_name.title()}"]
    if kingdom and kingdom in KINGDOM_CONSTRAINT:
        parts.append(KINGDOM_CONSTRAINT[kingdom])
    if aliases:
        parts.append(f"Also referred to as: {', '.join(aliases)}")
    if sample_chunk:
        parts.append(f"Journal excerpt:\n{sample_chunk.strip()}")
    user_content = "\n".join(parts)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
        )
        suggestion = resp.choices[0].message.content.strip()
        if not suggestion or suggestion.lower() == "unknown":
            return None
        return suggestion
    except Exception as exc:
        print(f"    LLM error: {exc}")
        return None


# ── Neo4j writes ──────────────────────────────────────────────────────────────

def update_canonical(driver, label: str, old_cn: str, new_cn: str) -> None:
    """
    Point the Species node at new_cn.

    - No conflict: simple rename; old canonicalName moves into aliases.
    - Conflict: merge the two nodes with APOC so all MENTIONED_IN and other
      relationships are consolidated onto the surviving node.
    """
    # Keep "sp." lowercase: "Anas sp." not "Anas Sp."
    display = new_cn.title().replace(" Sp.", " sp.")
    with driver.session() as s:
        conflict = s.run(
            f"MATCH (n:{label} {{canonicalName: $cn}}) RETURN count(*) AS c",
            cn=new_cn,
        ).single()["c"]

        if not conflict:
            # Simple rename
            s.run(
                f"MATCH (n:{label} {{canonicalName: $old}})"
                " SET n.canonicalName = $new,"
                "     n.name          = $display,"
                "     n.aliases       = [x IN coll.distinct(coalesce(n.aliases, []) + [$old])"
                "                        WHERE x <> $display]",
                old=old_cn, new=new_cn, display=display,
            )
        else:
            # Merge: consolidate both nodes; scientific name node wins
            s.run(f"""
                MATCH (common:{label}    {{canonicalName: $old}})
                MATCH (scientific:{label} {{canonicalName: $new}})
                WITH collect(scientific) + collect(common) AS nodes,
                     reduce(acc = [], n IN collect(scientific) + collect(common) |
                         acc + coalesce(n.aliases, []) + [n.name]
                     ) AS rawAliases
                CALL apoc.refactor.mergeNodes(nodes, {{
                    mergeRels: false,
                    produceSelfRel: false
                }})
                YIELD node
                SET node.canonicalName = $new,
                    node.name          = $display,
                    node.aliases       = [x IN coll.distinct(coalesce(node.aliases, []) + [$old])
                                          WHERE x <> $display]
            """, old=old_cn, new=new_cn, display=display)

def setup_constraint(driver) -> None:
    with driver.session() as s:
        s.run("""
            CREATE CONSTRAINT taxon_rank_name IF NOT EXISTS
            FOR (t:Taxon) REQUIRE (t.rank, t.name) IS NODE KEY
        """)


def write_taxonomy(driver, canonical: str, gbif: dict) -> None:
    """
    Upsert Taxon nodes for each rank present in the GBIF response, link them
    into a parent chain, and attach the Species node to its nearest taxon.
    """
    # Collect ranks that GBIF returned, from broadest to most specific
    ranks_present: list[tuple[str, str, int | None]] = []
    for rank in RANKS:
        name = gbif.get(rank)
        key  = gbif.get(f"{rank}Key")
        if name:
            ranks_present.append((rank, name, key))

    if not ranks_present:
        return

    with driver.session() as s:
        # Upsert each Taxon node
        for rank, name, key in ranks_present:
            s.run("""
                MERGE (t:Taxon {rank: $rank, name: $name})
                ON CREATE SET t.gbifKey = $key
            """, rank=rank, name=name, key=key)

        # Link each taxon to its parent (child BELONGS_TO parent)
        # ranks_present is ordered broadest→most-specific, so [i] is the child
        # (more specific) and [i-1] is the parent (less specific).
        for i in range(1, len(ranks_present)):
            parent_rank, parent_name, _ = ranks_present[i - 1]
            child_rank,  child_name,  _ = ranks_present[i]
            s.run("""
                MATCH (child:Taxon  {rank: $childRank,  name: $childName})
                MATCH (parent:Taxon {rank: $parentRank, name: $parentName})
                MERGE (child)-[:BELONGS_TO]->(parent)
            """, childRank=child_rank, childName=child_name,
                 parentRank=parent_rank, parentName=parent_name)

        # Link the Species node to its nearest taxon.
        # SP. nodes (genus-level) link to the genus Taxon; full binomials also
        # link to genus so the BELONGS_TO* path to class/kingdom always works.
        nearest_rank, nearest_name, _ = ranks_present[-1]  # most specific present
        s.run("""
            MATCH (s:Species {canonicalName: $cn})
            MATCH (t:Taxon {rank: $rank, name: $name})
            MERGE (s)-[:BELONGS_TO]->(t)
        """, cn=canonical, rank=nearest_rank, name=nearest_name)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("Connected to Neo4j.")

    setup_constraint(driver)

    with driver.session() as s:
        species = s.run("""
            MATCH (n:Species)
            WHERE NOT EXISTS { (n)-[:BELONGS_TO]->(:Taxon) }
            RETURN n.canonicalName AS canonicalName,
                   labels(n)      AS labels,
                   coalesce(n.aliases, []) AS aliases
            ORDER BY n.canonicalName
        """).data()

    total = len(species)
    print(f"Found {total} species nodes without taxonomy.\n")

    resolved = skipped = errors = 0

    for i, row in enumerate(species, 1):
        canonical = row["canonicalName"]
        aliases   = row["aliases"]
        # Derive the specific label (PlantSpecies or AnimalSpecies) and kingdom
        label   = next((l for l in row["labels"] if l != "Species"), "Species")
        kingdom = LABEL_KINGDOM.get(label)

        # Pass 1: direct GBIF lookup by scientific name
        gbif = query_gbif(canonical, kingdom)
        time.sleep(REQUEST_DELAY)

        # Pass 2: LLM suggests a full scientific binomial, then we verify with
        #         GBIF.  Two sub-passes:
        #           2a. genus + species  →  canonicalName stays as full binomial
        #           2b. genus only       →  canonicalName becomes GENUS SP.
        llm_new_cn = None   # the canonicalName update to apply, if any
        if gbif is None:
            sample_chunk = fetch_sample_chunk(driver, canonical)
            llm_suggestion = llm_scientific_name(
                client, canonical, aliases,
                kingdom=kingdom, sample_chunk=sample_chunk,
            )
            print(f"    [{canonical}] LLM suggestion: {llm_suggestion!r}")
            if llm_suggestion:
                words = llm_suggestion.split()
                if len(words) >= 2:
                    # 2a: LLM gave a full binomial — try genus + species first
                    gbif = query_gbif(llm_suggestion, kingdom, genus_only=False)
                    print(f"    [{canonical}] GBIF 2a (binomial): {gbif and {k: gbif.get(k) for k in ('matchType','confidence','genus','species','class','order')}}")
                    time.sleep(REQUEST_DELAY)
                    if gbif is not None:
                        llm_new_cn = llm_suggestion.upper()
                    else:
                        # 2b: binomial didn't match — fall back to genus only
                        genus = words[0]
                        gbif = query_gbif(genus, kingdom, genus_only=True)
                        print(f"    [{canonical}] GBIF 2b (genus):   {gbif and {k: gbif.get(k) for k in ('matchType','confidence','genus','class','order')}}")
                        time.sleep(REQUEST_DELAY)
                        if gbif is not None:
                            llm_new_cn = genus.upper() + " SP."
                else:
                    # LLM gave a genus directly — go straight to genus-only GBIF
                    gbif = query_gbif(llm_suggestion, kingdom, genus_only=True)
                    print(f"    [{canonical}] GBIF 2b (genus):   {gbif and {k: gbif.get(k) for k in ('matchType','confidence','genus','class','order')}}")
                    time.sleep(REQUEST_DELAY)
                    if gbif is not None:
                        llm_new_cn = llm_suggestion.upper() + " SP."

        if gbif is None:
            print(f"  [{i:>3}/{total}] SKIP   {canonical}")
            skipped += 1
            continue

        try:
            if llm_new_cn:
                update_canonical(driver, label, canonical, llm_new_cn)
                print(f"  [{i:>3}/{total}] LLM    {canonical}  →  '{llm_new_cn}'")

            effective_cn = llm_new_cn if llm_new_cn else canonical
            write_taxonomy(driver, effective_cn, gbif)
            cls   = gbif.get("class", "?")
            order = gbif.get("order", "?")
            print(f"  [{i:>3}/{total}] OK     {effective_cn}  →  {cls} / {order}")
            resolved += 1
        except Exception as exc:
            print(f"  [{i:>3}/{total}] ERROR  {canonical}: {exc}")
            errors += 1

    driver.close()
    print(f"\nResolved: {resolved}  Skipped: {skipped}  Errors: {errors}")
    print("Taxonomy enrichment complete.")


if __name__ == "__main__":
    main()

