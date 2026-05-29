#!/usr/bin/env python3
"""
Lewis & Clark Journals — entity disambiguation pipeline.

Phase 1 — Identify duplicates
  For each entity label, find candidate duplicate pairs using:
    a) GDS nodeSimilarity with cosine similarity on chunk co-occurrence vectors:
       an entity-entity graph is projected where edge weight is the number of
       chunks both entities are mentioned in together; cosine similarity on
       those weighted neighbourhood vectors finds entities with similar
       co-occurrence patterns.
    b) Jaro-Winkler string similarity on canonicalName
    c) Token containment on canonicalName tokens
    d) Shared aliases
  Send candidates to GPT-4o-mini, which confirms duplicates and picks the best
  canonical name. Confirmed pairs get an IS_SAME_ENTITY_AS relationship written
  between them — no merging yet.

  Case-only duplicates are already eliminated at extraction time because nodes
  merge on canonicalName (ALL CAPS).

Phase 2 — Merge components
  Run GDS WCC on the IS_SAME_ENTITY_AS graph per label. Each connected component
  is a set of nodes that should become one. Merge each component with APOC,
  setting canonicalName (ALL CAPS) and name (title case) on the surviving node
  and preserving all raw text forms as aliases.
"""

import argparse
import os
from collections import Counter
from pydantic import BaseModel

from dotenv import load_dotenv
from graphdatascience import GraphDataScience
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

NEO4J_URI        = os.environ["NEO4J_URI"]
NEO4J_USER       = os.environ["NEO4J_USER"]
NEO4J_PASSWORD   = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
RESOLUTION_MODEL = os.getenv("RESOLUTION_MODEL", "gpt-4o-mini")

LABELS = ["Person", "NativeNation", "Place", "WaterBody", "PlantSpecies", "AnimalSpecies", "Supply", "Event"]

COSINE_CUTOFF    = 0.5
JARO_CUTOFF      = 0.92
MIN_CHUNK_COUNT  = 2   # minimum chunk co-occurrences to include an edge
TOP_K            = 10

# Single-word Person names that are unambiguous throughout the corpus and may
# be merged with their multi-word equivalents.
UNAMBIGUOUS_SINGLE_NAMES: frozenset[str] = frozenset({
    "LEWIS", "CLARK", "SACAGAWEA", "CHARBONNEAU",
    "ORDWAY", "GASS", "PRYOR", "FLOYD",
})

# Title/rank tokens that appear as the first word in many Person names.
# Stripped before string comparison so "Sergt. Gass" and "Sergt. Pryor" are
# compared as "Gass" vs "Pryor" rather than sharing a high-weight prefix.
# Only applied when the name has more than one token (protects bare surnames).
TITLE_TOKENS: frozenset[str] = frozenset({
    # Sergeant variants (periods stripped, lower-cased)
    "sergt", "serjt", "sgt", "sarjt", "sjt", "seri",
    "serjeant", "sergeant", "sergiant",
    # Captain / Lieutenant
    "capt", "captain",
    "lt", "lts", "lieut", "lieuts", "lieutenant", "lieutenants",
    # Other military ranks
    "corp", "cpl", "corporal",
    "pvt", "private",
    "major", "maj",
    "col", "colonel",
    "gen", "general",
    # Honorifics
    "chief",
    "dr", "doctor",
    "mr", "mrs",
})

# ── Structured output ─────────────────────────────────────────────────────────

class ResolutionDecision(BaseModel):
    same_entity: bool
    canonical_name: str  # ignored if same_entity is False

# ── Prompt ────────────────────────────────────────────────────────────────────

RESOLUTION_PROMPT = """
You are an expert on the Lewis and Clark Expedition (1804-1806).

You will be given two entity names of the same type extracted from the expedition
journals. Determine whether they refer to the same real-world entity.

If they are the same entity, provide the best canonical name — the most complete,
modern, and widely accepted form. Examples of preferred forms:
  "Meriwether Lewis" not "Capt. Lewis" or "Lewis"
  "William Clark"    not "Capt. Clark" or "Clark"
  "Arikara"          not "Ricaras" or "Rickarees"
  "Hidatsa"          not "Minnetarees" or "Gros Ventres"
  "Shoshone"         not "Snake Indians"
  "Nez Perce"        not "Chopunnish"

If they are NOT the same entity, set same_entity to false. The canonical_name
field will be ignored.
""".strip()

SPECIES_RESOLUTION_PROMPT = """
You are a taxonomist and historian of natural history specialising in the Lewis
and Clark Expedition (1804-1806).

You will be given two species names extracted from the expedition journals.
Determine whether they refer to exactly the same biological species.

RULES — read carefully before deciding:

1. Same species only if they are the same taxon. Ecological similarity, shared
   habitat, or co-occurrence in the journals is NOT sufficient. When in doubt,
   set same_entity to false.

2. A broad common name that covers multiple species (e.g. "duck", "hawk",
   "eagle", "goat", "wolf", "deer", "squirrel") must NOT be merged with a
   specific scientific name unless you are certain the common name was used
   exclusively for that species in this corpus.

3. A genus-level placeholder (e.g. "ANAS SP.", "OVIS SP.", "CANIS SP.") should
   only be merged with a species-level name if the species is the sole member of
   that genus likely to appear in the Lewis and Clark journals AND the common
   name evidence is unambiguous.

4. Canonical name must be a valid binomial in ALL CAPS (e.g. "CANIS LUPUS") or
   GENUS SP. in ALL CAPS (e.g. "ANAS SP."). Never use a common name as the
   canonical name.

5. Known pairs that must NEVER be merged:
   - Wolf (CANIS LUPUS) ≠ coyote (CANIS LATRANS) ≠ domestic dog
   - Grouse (BONASA UMBELLUS) ≠ prairie hen (TYMPANUCHUS CUPIDO) ≠ wild turkey (MELEAGRIS GALLOPAVO)
   - Pronghorn/antelope (ANTILOCAPRA AMERICANA) ≠ mountain goat (OREAMNOS AMERICANUS) ≠ bighorn sheep (OVIS CANADENSIS)
   - White-tailed deer (ODOCOILEUS VIRGINIANUS) ≠ mule deer (ODOCOILEUS HEMIONUS)
   - Canada goose (BRANTA CANADENSIS) ≠ brant (BRANTA BERNICLA)
   - Cottonwood (POPULUS DELTOIDES) ≠ aspen (POPULUS TREMULOIDES)

If they are the same species, provide the accepted binomial in ALL CAPS as
canonical_name. If they are NOT the same species, set same_entity to false.
""".strip()

# ── Phase 1: candidate generation ─────────────────────────────────────────────

COOCCURRENCE_GRAPH = "entity-cooccurrence"


def build_cooccurrence_graph(gds: GraphDataScience, driver):
    """Project a single entity-entity graph weighted by chunk co-occurrence count.

    All entity types are included so cross-label edges enrich the similarity
    vectors. filteredNodeSimilarity then restricts pairs to same-label nodes.
    Returns (G, id_to_name) where id_to_name maps GDS node id → canonicalName.
    """
    try:
        gds.graph.get(COOCCURRENCE_GRAPH).drop()
    except Exception:
        pass

    G, _ = gds.graph.cypher.project("""
        MATCH (e1)-[:MENTIONED_IN]->(c:Chunk)<-[:MENTIONED_IN]-(e2)
        WHERE id(e1) < id(e2)
          AND NOT e1:Chunk AND NOT e2:Chunk
          AND e1.canonicalName IS NOT NULL
          AND e2.canonicalName IS NOT NULL
        WITH e1, e2, count(DISTINCT c) AS chunkCount
        WHERE chunkCount >= $minCount
        RETURN gds.graph.project(
            $graphName, e1, e2,
            {
                sourceNodeLabels: labels(e1),
                targetNodeLabels: labels(e2),
                relationshipType: 'MENTIONED_WITH',
                relationshipProperties: {chunkCount: chunkCount}
            },
            {undirectedRelationshipTypes: ['MENTIONED_WITH']}
        )
    """, graphName=COOCCURRENCE_GRAPH, minCount=MIN_CHUNK_COUNT)

    with driver.session() as s:
        rows = s.run("""
            MATCH (n) WHERE NOT n:Chunk AND n.canonicalName IS NOT NULL
            RETURN id(n) AS nid, n.canonicalName AS canonicalName
        """).data()
    id_to_name = {r["nid"]: r["canonicalName"] for r in rows}

    print(f"  Co-occurrence graph: {G.node_count()} nodes, {G.relationship_count()} relationships")
    return G, id_to_name


def gds_candidates(
    gds: GraphDataScience,
    G,
    id_to_name: dict,
    label: str,
) -> set[tuple[str, str]]:
    """Filtered cosine similarity on the shared co-occurrence graph."""
    try:
        results_df = gds.nodeSimilarity.filtered.stream(
            G,
            sourceNodeFilter=label,
            targetNodeFilter=label,
            topK=TOP_K,
            similarityCutoff=COSINE_CUTOFF,
            similarityMetric="COSINE",
            relationshipWeightProperty="chunkCount",
        )
    except Exception as e:
        print(f"    GDS error: {e}")
        return set()

    return {
        (min(id_to_name[row.node1], id_to_name[row.node2]),
         max(id_to_name[row.node1], id_to_name[row.node2]))
        for row in results_df.itertuples()
        if row.node1 in id_to_name and row.node2 in id_to_name
    }


def string_candidates(driver, label: str) -> set[tuple[str, str]]:
    """
    Two signals:
    1. Jaro-Winkler >= JARO_CUTOFF with first-character blocking.
    2. Token containment — all tokens of the shorter name appear in the longer
       name's token set (catches abbreviations across different initials).
    3. Double Metaphone token comparison — JW on the phonetic codes of each
       token pair >= 0.85.  Catches archaic/phonetic journal spellings where
       JW on the raw strings falls short of JARO_CUTOFF, e.g. the
       "Chabonah" family (XPN) vs "Charbonneau" family (XRPN): the metaphone
       codes differ by one insertion but their JW is ~0.925.

    For Person names, leading title tokens (Sergt., Capt., Chief, etc.) are
    stripped before comparison so rank-prefixed names for *different* people
    don't inflate similarity scores via a shared prefix.
    """
    titles = list(TITLE_TOKENS) if label == "Person" else []
    with driver.session() as s:
        rows = s.run(f"""
            MATCH (a:{label}), (b:{label})
            WHERE a.canonicalName < b.canonicalName
            WITH a, b,
                 [t IN split(toLower(a.canonicalName), ' ') | trim(replace(t, '.', ''))] AS tokA_raw,
                 [t IN split(toLower(b.canonicalName), ' ') | trim(replace(t, '.', ''))] AS tokB_raw
            WITH a, b,
                 CASE WHEN size(tokA_raw) > 1 AND head(tokA_raw) IN $titles
                      THEN tail(tokA_raw) ELSE tokA_raw END AS tokA,
                 CASE WHEN size(tokB_raw) > 1 AND head(tokB_raw) IN $titles
                      THEN tail(tokB_raw) ELSE tokB_raw END AS tokB
            WITH a, b, tokA, tokB,
                 apoc.text.join(tokA, ' ') AS strippedA,
                 apoc.text.join(tokB, ' ') AS strippedB
            WITH a, b, tokA, tokB, strippedA, strippedB,
                 apoc.text.jaroWinklerDistance(strippedA, strippedB) AS jw,
                 apoc.text.doubleMetaphone(last(tokA)) AS metLastA,
                 apoc.text.doubleMetaphone(last(tokB)) AS metLastB
            WHERE strippedA <> '' AND strippedB <> ''
              AND (
                (left(strippedA, 1) = left(strippedB, 1) AND jw >= {JARO_CUTOFF})
                OR (size(tokA) <= size(tokB) AND size(tokA) > 1 AND all(t IN tokA WHERE size(t) >= 3) AND all(t IN tokA WHERE t IN tokB))
                OR (size(tokB) <  size(tokA) AND size(tokB) > 1 AND all(t IN tokB WHERE size(t) >= 3) AND all(t IN tokB WHERE t IN tokA))
                OR (metLastA <> '' AND metLastB <> '' AND size(metLastA) >= 3 AND size(metLastB) >= 3
                    AND left(metLastA, 1) = left(metLastB, 1)
                    AND apoc.text.jaroWinklerDistance(metLastA, metLastB) >= 0.85)
              )
            RETURN a.canonicalName AS name1, b.canonicalName AS name2
        """, titles=titles).data()
    return {
        (r["name1"], r["name2"])
        for r in rows
        if r["name1"] and r["name2"]
    }


def alias_candidates(driver, label: str) -> set[tuple[str, str]]:
    """Pairs where nodes share an alias (case-insensitive), or one node's
    alias uppercased matches the other node's canonicalName — strong signal
    they are the same entity.

    Both directions are checked for the canonicalName match (a's alias vs
    b's canonical, and b's alias vs a's canonical) so the ordering of a < b
    does not cause misses.  Alias-vs-alias comparison is case-insensitive so
    "RATTLE SNAKE" and "rattle snake" are recognised as the same alias.
    """
    with driver.session() as s:
        rows = s.run(f"""
            MATCH (a:{label}), (b:{label})
            WHERE a.canonicalName < b.canonicalName
              AND (
                any(aa IN coalesce(a.aliases, []) WHERE
                      toUpper(aa) = b.canonicalName
                      OR any(ba IN coalesce(b.aliases, []) WHERE toLower(aa) = toLower(ba)))
                OR any(ba IN coalesce(b.aliases, []) WHERE
                      toUpper(ba) = a.canonicalName)
              )
            RETURN a.canonicalName AS name1, b.canonicalName AS name2
        """).data()
    return {(r["name1"], r["name2"]) for r in rows if r["name1"] and r["name2"]}


def get_candidates(
    gds: GraphDataScience,
    G,
    id_to_name: dict,
    driver,
    label: str,
) -> list[tuple[str, str]]:
    gds_pairs   = gds_candidates(gds, G, id_to_name, label)
    str_pairs   = string_candidates(driver, label)
    alias_pairs = alias_candidates(driver, label)
    all_pairs   = gds_pairs | str_pairs | alias_pairs

    # For Person, only merge multi-word names with multi-word names — or with a
    # known-unambiguous single-word name (e.g. "Lewis" always means Meriwether
    # Lewis). Generic single-word names like "Shannon" or "Collins" could refer
    # to different people in different chunks and are left alone.
    if label == "Person":
        def _allowed(n1: str, n2: str) -> bool:
            multi = " " in n1 and " " in n2
            one_famous = (n1 in UNAMBIGUOUS_SINGLE_NAMES or n2 in UNAMBIGUOUS_SINGLE_NAMES)
            return multi or one_famous
        all_pairs = {(n1, n2) for n1, n2 in all_pairs if _allowed(n1, n2)}

    print(f"  GDS: {len(gds_pairs)}  string: {len(str_pairs)}  alias: {len(alias_pairs)}  combined: {len(all_pairs)}")
    return list(all_pairs)


# ── Phase 1: LLM confirmation ─────────────────────────────────────────────────

SPECIES_LABELS = frozenset({"PlantSpecies", "AnimalSpecies"})

def confirm(client: OpenAI, label: str, name1: str, name2: str) -> ResolutionDecision | None:
    prompt = SPECIES_RESOLUTION_PROMPT if label in SPECIES_LABELS else RESOLUTION_PROMPT
    try:
        resp = client.beta.chat.completions.parse(
            model=RESOLUTION_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": f"Entity type: {label}\nName 1: {name1}\nName 2: {name2}"},
            ],
            response_format=ResolutionDecision,
        )
        return resp.choices[0].message.parsed
    except Exception as e:
        print(f"    LLM error ({name1} / {name2}): {e}")
        return None


def create_same_entity_rel(
    driver, label: str, cn1: str, cn2: str, canonical: str
) -> None:
    with driver.session() as s:
        s.run(
            f"MATCH (a:{label} {{canonicalName: $n1}}), (b:{label} {{canonicalName: $n2}})"
            " MERGE (a)-[:IS_SAME_ENTITY_AS {canonicalName: $canonical}]->(b)",
            n1=cn1, n2=cn2, canonical=canonical.strip().upper(),
        )


def identify_duplicates(
    gds: GraphDataScience, G, id_to_name: dict, driver, client: OpenAI, label: str
) -> None:
    print(f"\n── {label} (phase 1) ──")
    candidates = get_candidates(gds, G, id_to_name, driver, label)

    if not candidates:
        print("  No candidates.")
        return

    confirmed = rejected = 0
    for name1, name2 in candidates:
        decision = confirm(client, label, name1, name2)
        if not decision:
            continue
        if decision.same_entity:
            create_same_entity_rel(driver, label, name1, name2, decision.canonical_name)
            print(f"  LINK  '{name1}'  ~  '{name2}'  →  '{decision.canonical_name}'")
            confirmed += 1
        else:
            rejected += 1

    print(f"  {confirmed} confirmed  |  {rejected} rejected")

# ── Phase 2: WCC + merge ──────────────────────────────────────────────────────

def best_canonical(driver, label: str, node_ids: list[int]) -> str:
    """Pick the most-suggested canonical name from IS_SAME_ENTITY_AS edges
    in this component; fall back to the longest node name."""
    with driver.session() as s:
        rels = s.run(
            f"MATCH (a:{label})-[r:IS_SAME_ENTITY_AS]-(b:{label})"
            " WHERE id(a) IN $ids AND id(b) IN $ids"
            " RETURN r.canonicalName AS canonical",
            ids=node_ids,
        ).data()

    suggestions = [r["canonical"] for r in rels if r.get("canonical")]
    if suggestions:
        counts = Counter(suggestions)
        return max(counts, key=lambda x: (counts[x], len(x)))

    # Fallback: longest canonicalName in the component
    with driver.session() as s:
        names = s.run(
            f"MATCH (n:{label}) WHERE id(n) IN $ids RETURN n.canonicalName AS name",
            ids=node_ids,
        ).data()
    return max((r["name"] for r in names if r["name"]), key=len, default="")


def merge_component(driver, label: str, node_ids: list[int], canonical: str) -> None:
    display = canonical.title()
    with driver.session() as s:
        s.run(f"""
            MATCH (n:{label}) WHERE id(n) IN $ids
            WITH collect(n) AS nodes,
                 reduce(acc = [], n IN collect(n) |
                     acc + coalesce(n.aliases, []) + [n.name]
                 ) AS rawAliases
            CALL apoc.refactor.mergeNodes(nodes, {{
                mergeRels: false,
                produceSelfRel: false
            }})
            YIELD node
            SET node.canonicalName = $canonical,
                node.name          = $display,
                node.aliases       = [x IN apoc.coll.toSet(rawAliases) WHERE x <> $display]
        """, ids=node_ids, canonical=canonical, display=display)


def merge_duplicates(gds: GraphDataScience, driver, label: str) -> None:
    print(f"\n── {label} (phase 2) ──")

    # Skip if no IS_SAME_ENTITY_AS relationships exist for this label
    with driver.session() as s:
        count = s.run(
            f"MATCH (a:{label})-[:IS_SAME_ENTITY_AS]-(b:{label})"
            " RETURN count(*) AS c"
        ).single()["c"]
    if count == 0:
        print("  No IS_SAME_ENTITY_AS relationships — nothing to merge.")
        return

    graph_name = f"wcc-{label}"
    try:
        gds.graph.get(graph_name).drop()
    except Exception:
        pass

    G = None
    try:
        G, _ = gds.graph.project(
            graph_name,
            label,
            {"IS_SAME_ENTITY_AS": {"orientation": "UNDIRECTED"}},
        )

        components_df = gds.wcc.stream(G)

    finally:
        if G is not None:
            G.drop()

    # Group node IDs by component; only process components with >1 node
    component_groups = (
        components_df.groupby("componentId")["nodeId"]
        .apply(list)
        .reset_index()
    )
    multi = component_groups[component_groups["nodeId"].apply(len) > 1]

    print(f"  {len(multi)} components to merge")
    merged = 0
    for _, row in multi.iterrows():
        node_ids  = list(row["nodeId"])
        canonical = best_canonical(driver, label, node_ids)
        if not canonical:
            continue
        canonical = canonical.strip().upper()

        # If a node with the target canonicalName already exists outside this
        # component, pull it in so APOC merges all of them together and the
        # subsequent SET doesn't collide with the uniqueness constraint.
        with driver.session() as s:
            extras = s.run(
                f"MATCH (n:{label} {{canonicalName: $cn}}) WHERE NOT id(n) IN $ids"
                " RETURN id(n) AS nid",
                cn=canonical, ids=node_ids,
            ).data()
        for r in extras:
            node_ids.append(r["nid"])

        # Fetch canonical names for logging
        with driver.session() as s:
            names = [
                r["name"] for r in s.run(
                    f"MATCH (n:{label}) WHERE id(n) IN $ids RETURN n.canonicalName AS name",
                    ids=node_ids,
                ).data()
            ]
        print(f"  MERGE {names}  →  '{canonical}'")
        merge_component(driver, label, node_ids, canonical)
        merged += 1

    print(f"  {merged} components merged")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Entity disambiguation pipeline.")
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2],
        default=None,
        help="Run only phase 1 (identify) or phase 2 (merge). Omit to run both.",
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        default=None,
        metavar="LABEL",
        help="Restrict to specific labels, e.g. --labels AnimalSpecies PlantSpecies",
    )
    args = parser.parse_args()

    labels = args.labels if args.labels else LABELS
    invalid = set(labels) - set(LABELS)
    if invalid:
        parser.error(f"Unknown labels: {', '.join(sorted(invalid))}. Valid: {', '.join(LABELS)}")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    gds    = GraphDataScience(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("Connected to Neo4j.")

    if args.phase in (1, None):
        print("\n═══ Phase 1: identify duplicates ═══")
        G, id_to_name = build_cooccurrence_graph(gds, driver)
        try:
            for label in labels:
                identify_duplicates(gds, G, id_to_name, driver, client, label)
        finally:
            G.drop()

    if args.phase in (2, None):
        print("\n═══ Phase 2: merge components ═══")
        for label in labels:
            merge_duplicates(gds, driver, label)
        deleted = 0
        with driver.session() as s:
            for label in labels:
                result = s.run(
                    f"MATCH (a)-[r:IS_SAME_ENTITY_AS]-(b)"
                    f" WHERE a:{label} OR b:{label}"
                    f" DELETE r RETURN count(*) AS n"
                )
                deleted += result.single()["n"]
        print(f"\nDeleted {deleted} IS_SAME_ENTITY_AS relationships.")

    driver.close()
    gds.close()
    print("\nDisambiguation complete.")


if __name__ == "__main__":
    main()
