#!/usr/bin/env python3
"""
Lewis & Clark Journals — knowledge graph extraction pipeline.

Uses OpenAI structured outputs to extract entities and relationships from
each Chunk node, then writes them to Neo4j directly via the Python driver.
MENTIONED_IN relationships link extracted entities back to their source chunks.

Re-run safe: chunks are marked graphExtracted=true on success and skipped.
"""

import asyncio
import os
import re
from difflib import SequenceMatcher
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
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")
CONCURRENCY      = int(os.getenv("EXTRACTION_CONCURRENCY", "5"))

# ── Schema ────────────────────────────────────────────────────────────────────

VALID_NODE_LABELS = frozenset({
    "Person", "Place", "WaterBody", "NativeNation",
    "PlantSpecies", "AnimalSpecies",
    "Supply", "Event",
})

# These labels get a secondary :Species label so `MATCH (n:Species)` returns both.
SPECIES_LABELS = frozenset({"PlantSpecies", "AnimalSpecies"})

VALID_REL_TYPES = frozenset({
    "MEMBER_OF", "VISITED", "CAMPED_AT", "MET_WITH", "OBSERVED",
    "TRADED_WITH", "ACQUIRED_PROVISION", "GUIDED", "INTERPRETED_FOR", "NAMED",
    "ORIGINATED_FROM", "PARTICIPATED_IN",
})

_P  = frozenset({"Person"})
_PN = frozenset({"Person", "NativeNation"})
_PW = frozenset({"Place", "WaterBody"})
_SP = frozenset({"PlantSpecies", "AnimalSpecies"})

# Maps each relationship type to (allowed start labels, allowed end labels).
# A relationship is dropped if either endpoint's label is not in the allowed set.
REL_SCHEMA: dict[str, tuple[frozenset, frozenset]] = {
    "MEMBER_OF":       (_P,            frozenset({"NativeNation"})),
    "VISITED":         (_P,            _PW),
    "CAMPED_AT":       (_P,            _PW),
    "MET_WITH":        (_PN,           _PN),
    "OBSERVED":        (_P,            _SP | _PW),
    "TRADED_WITH":        (_PN,           _PN),
    "ACQUIRED_PROVISION": (_PN,           frozenset({"Supply"})),
    "GUIDED":          (_P,            _P),
    "INTERPRETED_FOR": (_P,            frozenset({"NativeNation"})),
    "NAMED":           (_P,            _PW),
    "ORIGINATED_FROM": (_PN,           frozenset({"NativeNation", "Place", "WaterBody"})),
    "PARTICIPATED_IN":  (_PN,           frozenset({"Event"})),
}

# ── Structured output models ──────────────────────────────────────────────────

class ExtractedNode(BaseModel):
    id: str            # local reference ID used only to define relationships below
    label: str         # must be one of VALID_NODE_LABELS
    name: str          # name as it appears in the text (preserved as provenance)
    canonicalName: str # ALL CAPS stable identifier; used as the merge key

class ExtractedRelationship(BaseModel):
    start_node_id: str   # references ExtractedNode.id
    end_node_id: str     # references ExtractedNode.id
    type: str            # must be one of VALID_REL_TYPES

class ExtractionResult(BaseModel):
    nodes: list[ExtractedNode]
    relationships: list[ExtractedRelationship]

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a knowledge graph extraction engine. Extract entities and relationships
from journal text written during the Lewis and Clark Expedition (1804-1806).

ENTITY TYPES:
- Person        A human being: expedition members, Native Americans, officials
- WaterBody     A named river, creek, tributary, falls, or lake with a SPECIFIC
                PROPER NAME (e.g., "Missouri River", "Platte River", "Great
                Falls"). DO NOT extract bare generic terms with no proper name
                ("River", "Creek", "Lake", "Falls", "Spring").
- Place         A named terrestrial location with a SPECIFIC PROPER NAME:
                fort, mountain, valley, pass, or region that has been given a
                name (e.g., "Fort Mandan", "Bitterroot Mountains", "Traveler's
                Rest", "Pompey's Pillar").
                DO NOT extract: water bodies (rivers, creeks, lakes — use
                WaterBody instead), bare common nouns with no proper name
                ("Point", "Lake", "Island", "Hill", "Cave", "Bottom", "Bluff",
                "Prairie", "Village", "Camp"), or any term that is merely a
                geographic feature type rather than a named place.
                NEVER extract navigational side-of-river references as Place
                or WaterBody nodes. These are relative positions, not locations:
                "S. S." (starboard side), "L. S." (larboard side), "Lard."
                "Lard Side", "Stard.", "Stard Side", "Larbord Shore",
                "Stard Shore", "N. E. Side", "S. W. Side", "Left Side",
                "Right Side", "N. Bank", "S. Bank", or any similar phrase
                describing which bank or side of the river the party was on.
- NativeNation  A Native American nation, tribe, or band
- PlantSpecies  A plant species observed, collected, or described
- AnimalSpecies A bird, mammal, fish, reptile, or other animal observed or described
- Supply        Food, medicine, equipment, weapons, or trade goods
- Event         A significant, non-routine occurrence with historical weight:
                a formal council with a Native nation, a battle or armed
                confrontation, a major portage, a death or burial, a notable
                ceremony, or a singular milestone (e.g., "Birth of
                Charbonneau's Son", "Christmas Celebration", "Attempted
                Theft by Blackfeet", "Crossing the Continental Divide",
                "Death of Sergeant Floyd").
                Canonicalize deaths as "DEATH OF [FULL NAME]" — e.g., a
                passage recording that Sergeant Floyd died must yield an
                Event node with canonicalName "DEATH OF CHARLES FLOYD".
                DO NOT extract: routine daily activities ("Camping",
                "Setting Out", "Canoes Loaded", "Ascended the River",
                "Captain Clark Set Out"), navigation descriptions, weather
                observations, or anything that happened most days of the
                journey.

CANONICAL NAME RULES:
- name: copy the term EXACTLY as it appears in the journal text. This is the
  raw provenance form. For species, this will be a common name ("cottonwood",
  "beaver", "wild grape") — never a scientific name.
- canonicalName: ALL CAPS stable identifier used to merge duplicates.
    Person: full name, no titles or ranks.
        "MERIWETHER LEWIS"    ← Lewis, Capt. Lewis, Captain Lewis
        "WILLIAM CLARK"       ← Clark, Capt. Clark, Captain Clark
        Use the canonicalization table below for known figures.
        CRITICAL: Meriwether Lewis and William Clark are always two distinct
        Person nodes. When both appear in the same passage, emit two separate
        nodes. Never assign Lewis references as aliases of Clark or vice versa.
        NEVER use a death, birth, or event phrase as a Person canonicalName.
        "Death of Sergeant Floyd", "the death of Floyd", "birth of a son" —
        these describe Events, not people. Extract the person separately
        (canonicalName="CHARLES FLOYD") and the event separately
        (canonicalName="DEATH OF CHARLES FLOYD").
        NEVER include "&" in a Person name or canonicalName. A phrase like
        "R. & Jo. Fields" or "Lewis & Clark" refers to two people — extract
        each as a separate Person node.
    PlantSpecies / AnimalSpecies: scientific name in ALL CAPS if you are
        certain of the identification. If uncertain, use the actual genus
        followed by "SP." only if you are confident of the genus. Otherwise
        use the common name in ALL CAPS. Never guess — a wrong scientific name
        silently merges unrelated species into one node.
        Each distinct organism gets its own canonicalName. Do not group
        different species together just because they are ecologically similar.
        These are all separate species — never merge them:
        - Prairie dog (CYNOMYS LUDOVICIANUS) ≠ prairie hen (TYMPANUCHUS
          CUPIDO) ≠ hare (LEPUS SP.) ≠ rabbit (SYLVILAGUS SP.) ≠ porcupine
          (ERETHIZON DORSATUM) ≠ otter (LONTRA CANADENSIS)
        - Prairie wolf / prairie wolves → coyote (CANIS LATRANS), NOT wolf
          (CANIS LUPUS). Domestic dog → do not extract as AnimalSpecies.
        - Elk (CERVUS CANADENSIS) ≠ white-tailed deer (ODOCOILEUS VIRGINIANUS)
          ≠ mule deer / black-tailed deer (ODOCOILEUS HEMIONUS)
        - Bighorn sheep (OVIS CANADENSIS) ≠ mountain goat (OREAMNOS AMERICANUS)
        - Canada goose (BRANTA CANADENSIS) ≠ brant (BRANTA BERNICLA). A brant
          is never a synonym for Canada goose.
        - Cottonwood (POPULUS DELTOIDES) ≠ aspen (POPULUS TREMULOIDES)
        - Wild plum (PRUNUS AMERICANA) ≠ chokecherry/wild cherry (PRUNUS VIRGINIANA)
        - Currant/gooseberry (RIBES SP.) ≠ raspberry (RUBUS IDAEUS) ≠
          blackberry (RUBUS ALLEGHENIENSIS) ≠ strawberry (FRAGARIA VIRGINIANA) ≠
          serviceberry (AMELANCHIER ALNIFOLIA) ≠ huckleberry (VACCINIUM SP.)
        - Do NOT use RIBES SP. as a catch-all for any unidentified berry. If
          the text says only "berries" with no further description, use the
          common name "BERRIES" as canonicalName rather than guessing a genus.
        Never put the scientific name in `name`.
        name="cottonwood"  canonicalName="POPULUS DELTOIDES"
        name="beaver"      canonicalName="CASTOR CANADENSIS"
        name="wild grape"  canonicalName="VITIS SP."
        name="wild rose"   canonicalName="ROSA SP."
        name="grouse"      canonicalName="GROUSE"
    All other labels: normalized ALL CAPS form of the name as found in text.
        "FORT MANDAN"         ← Fort Mandan
        "MISSOURI RIVER"      ← Missouri, Missouri R.
        "SHOSHONE"            ← Snake Indians, Shoshoni
- NEVER use "UNKNOWN", "N/A", entity type names ("Supply", "Place", "Person",
  etc.), or any other placeholder as canonicalName. If you cannot determine a
  canonical name, use the name exactly as it appears in the text in ALL CAPS.
  Every entity must have its own distinct canonicalName — do not group
  unrelated entities under a shared placeholder.

RELATIONSHIP TYPES:
- MEMBER_OF       Person → NativeNation (person is ethnically or culturally a member of
                  that nation — e.g., Sacagawea is Shoshone, a named individual is
                  identified as belonging to a tribe).
                  NEVER use this to link expedition members to a Native Nation they are
                  visiting, camped near, or mentioned alongside. Corps of Discovery
                  members (Lewis, Clark, Ordway, Gass, Pryor, Drouillard, York, etc.)
                  are never MEMBER_OF any Native Nation.
- VISITED         Person → Place or WaterBody
- CAMPED_AT       Person → Place
- MET_WITH        Person ↔ Person or NativeNation
- OBSERVED        Person → Species, Place, or WaterBody (saw, noted, or formally described)
- TRADED_WITH        Person or NativeNation ↔ Person or NativeNation (parties exchanging goods with each other)
- ACQUIRED_PROVISION Person or NativeNation → Supply (obtained or received a specific supply item such as food, horses, canoes, or trade goods)
- GUIDED          Person → Person (served as geographic or cultural guide)
- INTERPRETED_FOR Person → NativeNation (served as language interpreter)
- NAMED           Person → Place or WaterBody (gave a geographic name)
- ORIGINATED_FROM Person → NativeNation or Place (ethnic or geographic origin)
- PARTICIPATED_IN  Person or NativeNation → Event (took part in a significant occurrence)

CANONICALIZATION — always use the name in the left column regardless of how the
text spells or abbreviates it:

  Meriwether Lewis          ← Lewis, Capt. Lewis, Captain Lewis, Capt Lewis
  William Clark             ← Clark, Capt. Clark, Captain Clark, Capt Clark
  Sacagawea                 ← Sacajawea, Sah-ca-gar-we-ah, Sahkahgarwea, any variant
  Toussaint Charbonneau     ← Charbono, Shabonee, Charbonneau
  Jean Baptiste Charbonneau ← Pomp, Pompey
  George Drouillard         ← Drewyer, Drewger, Druyer
  York                      ← (Clark's enslaved companion; no other name known)
  John Ordway               ← Ordway, Sergt. Ordway
  Patrick Gass              ← Gass, Sergt. Gass
  Nathaniel Pryor           ← Pryor, Sergt. Pryor
  Charles Floyd             ← Floyd, Sergt. Floyd, Serjeant Floyd, Serj. Floyd, Sergt. C. Floyd
  Joseph Field              ← Jo Field, Jo Fields, Jos. Field, J. Field, Joseph Fields
  Reubin Field              ← R. Field, R. Fields, Reuben Field, Reuben Fields
  John B. Thompson          ← Thompson, J. B. Thompson, J. Thompson, Tompson
  Thomas Jefferson          ← President Jefferson, Mr. Jefferson
  Sioux                     ← Souis, Seaux, Soux, Souix
  Arikara                   ← Ricaras, Rickarees, Rees, Aricara
  Hidatsa                   ← Minnetarees, Gros Ventres, Minetares
  Shoshone                  ← Snake Indians, Shoshoni
  Nez Perce                 ← Chopunnish, Pierced Nose
  Blackfeet                 ← Blackfoot, Black feet
  Kansa                     ← Kansas, Kanzas, Kansais, Konza
  Osage                     ← Osages, Grand Osage, Little Osage, Great Osage
  Omaha                     ← Mahar, Mahars, Maha, Omahas
  Pawnee                    ← Paunees, Pania, Panies, Panis
  Oto                       ← Otteau, Otteaus, Otto, Ottoes
  Iowa                      ← Aieways, Aiauway, Iaway, Iowas
  Sauk                      ← Saukee, Saukees, Sac
  Missouri                  ← Missouries, Missouris (the Missouri people)
  Mandan                    ← Mandans
  Yankton Sioux             ← Yankton, Yanctons, Yancton Sioux
  Teton Sioux               ← Tetons, Teton, Lakota
  Cheyenne                  ← Chyenne, Chien, Shyenne, Shaien
  Assiniboine               ← Assiniboin, Ossiniboin, Assinniboin, Stone Indians

SPECIES CANONICALIZATION — use the scientific name as canonicalName in ALL CAPS.
If a text refers to a juvenile or sex variant, map it to the parent species.
When the species is uncertain, use the actual genus followed by "SP." in ALL
CAPS (e.g., "SALIX SP." for an unidentified willow, "JUGLANS SP." for an
unidentified walnut). Never use the literal string "GENUS SP." — always use
the real genus name. If the genus is also unknown, use the common name in ALL
CAPS rather than a placeholder.

  AnimalSpecies:
  Ursus arctos horribilis   ← grizzly bear, white bear, grisley bear, brown bear
  Ursus americanus          ← black bear
  Bison bison               ← buffalo, bison, buffaloe, bull, cow (when bison)
  Cervus canadensis         ← elk, wapiti
  Odocoileus virginianus    ← white-tailed deer, deer (eastern range), common deer
  Odocoileus hemionus       ← mule deer, black-tailed deer, black tale deer, deer (western range)
  Antilocapra americana     ← antelope, pronghorn, goat (on plains)
  Ovis canadensis           ← bighorn sheep, mountain sheep, bighorned animal, bighorned animals, bighorn animal, Argalia, big horn animal
  Oreamnos americanus       ← mountain goat, white goat, ibex
  Canis lupus               ← wolf, grey wolf, wolves, white wolf, white wolves
  Canis latrans             ← coyote, prairie wolf, prairie wolves, small wolf
  Puma concolor             ← mountain lion, cougar, panther
  Taxidea taxus             ← badger
  Castor canadensis         ← beaver
  Lontra canadensis         ← river otter, otter
  Cynomys ludovicianus      ← prairie dog, barking squirrel, ground rat
  Branta canadensis         ← Canada goose, wild goose, gosling, canadian goose
  Branta bernicla           ← brant, brants, white brant, gray brant, brown brant, common brant
  Meleagris gallopavo       ← wild turkey, turkey
  Haliaeetus leucocephalus  ← bald eagle, white-headed eagle
  Aquila chrysaetos         ← golden eagle
  Pica hudsonia             ← magpie, black-billed magpie
  Nucifraga columbiana      ← Clark's crow, Clark's nutcracker
  Melanerpes lewis          ← Lewis's woodpecker
  Oncorhynchus clarkii      ← cutthroat trout, trout (mountain streams)
  Oncorhynchus tshawytscha  ← Chinook salmon, salmon
  Acipenser sp.             ← sturgeon
  Polyodon spathula         ← paddlefish, spoonbill cat
  Crotalus viridis          ← prairie rattlesnake, rattlesnake (plains)
  Lepus sp.                 ← hare, hares, jackrabbit, hare of the prairie, prairie hare
  Sylvilagus sp.            ← rabbit, rabbits, cottontail
  Erethizon dorsatum        ← porcupine
  Tympanuchus cupido        ← prairie hen, prairie chicken, prairie cock, heath hen
  Bonasa umbellus           ← grouse, pheasant, prairie fowl, black pheasant
  Meleagris gallopavo       ← wild turkey, turkey

  PlantSpecies:
  Camassia quamash          ← camas, quamash, commass
  Sagittaria latifolia      ← wapato, wapatoo
  Artemisia tridentata      ← sagebrush, sage
  Populus deltoides         ← cottonwood, cotton wood, cotton tree, cotton timber
  Populus tremuloides       ← aspen, aspin, trembling aspen
  Salix sp.                 ← willow, willow tree
  Pinus ponderosa           ← ponderosa pine, yellow pine
  Pseudotsuga menziesii     ← Douglas fir, fir
  Thuja plicata             ← western red cedar, cedar
  Prunus virginiana         ← chokecherry, choke cherry, wild cherry, wild cherries, cherry, cherries
  Prunus americana          ← wild plum, plum, plums, plumb, wild plumb, plumbs, Osage plum, Osage plumb
  Amelanchier alnifolia     ← serviceberry, sarvis berry
  Ribes sp.                 ← currant, currents, gooseberry, gooseberries, Goosberries, goose berry, blue currant, blue current
  Rubus idaeus              ← raspberry, raspberries, raspburies, red raspberry, wild raspberry
  Rubus allegheniensis      ← blackberry, blackberries, black berry
  Fragaria virginiana       ← wild strawberry, strawberry, strawberries
  Opuntia polyacantha       ← prickly pear, prickly pear cactus
  Helianthus annuus         ← sunflower
  Allium sp.                ← wild onion, wild onions, small onion, small onions, onion, garlic
  Lonicera sp.              ← honeysuckle, honey suckle, wild honeysuckle
  Pediomelum sp.            ← ground potato, white apple, prairie turnip, tipsin
  Elymus sp.                ← wild rye, wild rye grass
  Helianthus tuberosus      ← wild artichoke, wild artichokes, Jerusalem artichoke
  Agastache sp.             ← hyssop, hysop, giant hyssop
  Typha latifolia           ← cattail, bulrush
  Rosa sp.                  ← wild rose, rosebush, rose
  Vaccinium sp.             ← huckleberry, cranberry, high bush cranberry
  Crataegus sp.             ← red haw, hawthorn
  Chenopodium album         ← lambsquarter, lamb's quarter
  Viburnum sp.              ← highbush cranberry
  Pediomelum sp.            ← white apple, prairie turnip, tipsin
  Juglans nigra             ← black walnut, walnut
  Fraxinus sp.              ← ash, ash tree
  Morus rubra               ← mulberry, red mulberry
  Tilia americana           ← linden, basswood
  Platanus occidentalis     ← sycamore, buttonwood
  Acer sp.                  ← maple, sugar maple
  Quercus sp.               ← oak, oak tree
  Ulmus sp.                 ← elm, elm tree

RULES:
- Assign each node a short local id (e.g. "0", "1") used only to reference
  nodes in relationships. Do not reuse ids across nodes.
- Only emit node labels and relationship types from the lists above.
- Only extract a PlantSpecies or AnimalSpecies if the text names an actual
  organism. Do not extract water, weather, terrain, or food preparation methods
  as species.
- When in doubt between WaterBody and Place, prefer WaterBody for anything
  involving water (river, creek, fork, rapids, falls, lake, bay, strait,
  sound, inlet, pond, spring, brook, stream).
- Forts and camps → Place (not Event).
- Nations and tribes → NativeNation (not Person or Place). Only extract a
  NativeNation if it is a specific named Native American nation, tribe, or band
  (e.g., "Shoshone", "Mandan", "Arikara"). Do not extract European or
  Euro-American groups (French, Canadian French, British, Spanish, Americans)
  as NativeNation — these are traders, soldiers, or settlers, not Native nations.
  Do not extract individual personal names (e.g., "Francois") as NativeNation.
  Do not extract vague collective terms ("Indians", "Aboriginees of America",
  "natives", "savages") as NativeNation. If a name contains "River", "Creek",
  or other water terms it is a WaterBody, not a NativeNation — even if a nation
  shares that name (e.g., "Kansas River" → WaterBody, "Kansas" → NativeNation).
- Do not extract journal directional abbreviations ("L. S.", "S. S.") as any
  entity type — they indicate riverbank side, not a place or thing.
- Plants, trees, roots, and berries → PlantSpecies (not AnimalSpecies or Supply).
- Birds, mammals, fish, and reptiles → AnimalSpecies (not PlantSpecies).
- "Venison", "jerked meat", "dried meat", and other food preparations → Supply,
  not AnimalSpecies. The species (deer, elk, bison) should be extracted
  separately if the animal itself is mentioned.
- Domestic "dog" or "dogs" owned by expedition members → do not extract as
  AnimalSpecies.
- Do not use ALLIUM SP. as a catch-all for unidentified plants. Each plant
  mentioned in the text should be mapped to its own entry in the species table,
  or left at a common-name canonicalName if genus is unknown.
- Cheyenne and Assiniboine are distinct NativeNation nodes — never merge them
  into Sioux or Osage.
- Events must be singular and significant — if it happened every day it is not
  an Event. Geographic features (bends, bluffs, rapids) are Place or WaterBody,
  not Event.
- If the same entity appears under different names in the text, emit it once
  using the canonical name above.
- Each person in a passage is a distinct Person node with their own canonicalName.
  The name field must be a spelling or abbreviation used in this passage to refer
  to that specific individual — never a name belonging to a different person who
  happens to appear nearby. Do not assign a co-occurring person's name as the
  name or canonicalName of someone else.
- Death and birth phrases are Event nodes, not Person nodes. Never emit a Person
  node whose canonicalName starts with "DEATH OF" or "BIRTH OF".
- If nothing extractable is found, return empty lists.
""".strip()

# ── Neo4j setup ───────────────────────────────────────────────────────────────

def setup_constraints(driver) -> None:
    with driver.session() as s:
        # Drop old name-based constraints if they exist (schema migration)
        for label in VALID_NODE_LABELS:
            s.run(f"DROP CONSTRAINT {label.lower()}_name IF EXISTS")
        for label in VALID_NODE_LABELS:
            s.run(
                f"CREATE CONSTRAINT {label.lower()}_canonical_name IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.canonicalName IS UNIQUE"
            )
    print("Node uniqueness constraints ready.")

# ── Neo4j writes ──────────────────────────────────────────────────────────────

import re as _re
_BARE_INITIAL = _re.compile(r'^[A-Z]{1,2}\.?$')

def _is_bare_initial(cn: str) -> bool:
    """True for single-letter initials (Y, W., MR., C.) that are not real entities."""
    return bool(_BARE_INITIAL.match(cn))


# Title/rank tokens stripped before alias plausibility checks (must stay in
# sync with TITLE_TOKENS in disambiguate.py).
_TITLE_TOKENS: frozenset[str] = frozenset({
    "sergt", "serjt", "sgt", "sarjt", "sjt", "seri",
    "serjeant", "sergeant", "sergiant",
    "capt", "captain",
    "lt", "lts", "lieut", "lieuts", "lieutenant", "lieutenants",
    "corp", "cpl", "corporal",
    "pvt", "private",
    "major", "maj",
    "col", "colonel",
    "gen", "general",
    "chief",
    "dr", "doctor",
    "mr", "mrs", "messers"
})


def _alias_plausible(raw: str, canonical: str) -> bool:
    """Return True if *raw* plausibly refers to *canonical*.

    Strips title/rank tokens from both strings, then tests each pair of
    (alias token, canonical token) with three signals in order:

    1. Exact match or prefix containment — fast path for clean variants.
    2. SequenceMatcher ratio ≥ 0.65 — catches phonetic / archaic spellings
       like "Chabonah" / "CHARBONNEAU" (≈ 0.78) while still rejecting
       clearly wrong names like "Gass" / "ORDWAY" (≈ 0.18) or
       "Durion" / "ORDWAY" (≈ 0.33).

    Only applied to Person nodes; other label types keep their canonical
    names even when the raw text looks very different (e.g. Teton Sioux →
    LAKOTA).
    """
    def _tokens(s: str) -> list[str]:
        cleaned = re.sub(r"[^a-z0-9\s]", "", s.lower())
        return [t for t in cleaned.split()
                if t not in _TITLE_TOKENS and len(t) >= 3]

    alias_toks = _tokens(raw)
    canon_toks = _tokens(canonical)
    if not alias_toks or not canon_toks:
        return False   # bare title ("Serjeant") or empty canonical
    for a in alias_toks:
        for c in canon_toks:
            if a == c or a.startswith(c) or c.startswith(a):
                return True
            if SequenceMatcher(None, a, c).ratio() >= 0.65:
                return True
    return False


# Hard alias → canonicalName reroutes for the most important figures.
# If the LLM assigns the wrong canonicalName but the raw name text is
# unambiguous, we correct the canonical before any MERGE runs so that
# relationships also land on the right node.
_ALIAS_REROUTE: dict[str, str] = {
    raw: cn
    for cn, raws in {
        # ── People ────────────────────────────────────────────────────────────
        "MERIWETHER LEWIS": {
            "lewis", "capt. lewis", "captain lewis", "cap lewis", "cap. lewis",
            "meriwether lewis", "m. lewis", "meriweather lewis",
            "capt l", "cap l.", "c. l.", "capt louis", "captn. lewis",
            "capt lewis", "cap. lewis",
        },
        "WILLIAM CLARK": {
            "clark", "capt. clark", "captain clark", "cap clark", "cap. clark",
            "william clark", "w. clark", "wm. clark", "captn. clark",
            "cpt. clark", "capt. w. clark", "win clark",
        },
        "SACAGAWEA": {
            "sacagawea", "sacajawea", "sah-ca-gar-we-ah", "sahkahgarwea",
        },
        "GEORGE DROUILLARD": {
            "drewyer", "drewger", "druyer", "george drouillard",
        },
        "JOSEPH FIELD": {
            "jo field", "jo. field", "jo fields", "jo. fields",
            "jos field", "jos. field", "jos. fields", "joseph fields",
        },
        "REUBIN FIELD": {
            "r. field", "r field", "r. fields", "r fields",
            "reubin field", "reuben field", "reuben fields",
        },
        "JOHN B. THOMPSON": {
            "j. b. thompson", "j.b. thompson", "j. b thompson",
            "j. thompson", "tompson",
        },
        # ── Native Nations ────────────────────────────────────────────────────
        "CHEYENNE": {
            "cheyenne", "chyenne", "shyenne", "shaien", "chien",
        },
        "ASSINIBOINE": {
            "assiniboine", "assiniboin", "ossiniboin", "assinniboin",
            "stone indians",
        },
        # ── Places / Water Bodies ─────────────────────────────────────────────
        # French and archaic English names that share no tokens with the
        # modern canonical — string similarity is too low for the
        # disambiguator to catch these automatically.
        "YELLOWSTONE RIVER": {
            # Clark's phonetic spellings of French "Roche Jaune"
            "rochejhone", "rochejhone river", "rochejhone r.",
            "roche jaune", "roche jaune river",
            # Variant English spellings in the journals
            "yellow stone", "yellow stone river", "yellow stone r.",
            "yellowstone", "yellowstone river",
        },
        "MILK RIVER": {
            "river that scolds at all others", "the river that scolds",
        },
        # ── Animal species ────────────────────────────────────────────────────
        "CANIS LATRANS": {
            "coyote", "prairie wolf", "prairie wolves", "small wolf",
        },
        "BRANTA BERNICLA": {
            "brant", "brants", "white brant", "gray brant", "brown brant",
            "common brant",
        },
        "OREAMNOS AMERICANUS": {
            "mountain goat", "white goat", "ibex",
        },
        "ODOCOILEUS HEMIONUS": {
            "mule deer", "black-tailed deer", "black tailed deer",
            "black tale deer", "black tale doe",
        },
        # ── Plant species ─────────────────────────────────────────────────────
        "POPULUS TREMULOIDES": {
            "aspen", "aspin", "trembling aspen",
        },
        "PRUNUS VIRGINIANA": {
            "chokecherry", "choke cherry", "wild cherry", "wild cherries",
            "cherry", "cherries",
        },
        "PRUNUS AMERICANA": {
            "wild plum", "wild plums", "plumb", "wild plumb", "plumbs",
            "wild plumbs", "osage plum", "osage plumb",
        },
        "RUBUS IDAEUS": {
            "raspberry", "raspberries", "raspburies", "red raspberry",
            "wild raspberry",
        },
        "RUBUS ALLEGHENIENSIS": {
            "blackberry", "blackberries", "black berry",
        },
        "FRAGARIA VIRGINIANA": {
            "wild strawberry", "strawberry", "strawberries",
        },
        "LONICERA SP.": {
            "honeysuckle", "honey suckle", "wild honeysuckle",
        },
        "PEDIOMELUM SP.": {
            "ground potato", "white apple", "prairie turnip", "tipsin",
        },
        "ELYMUS SP.": {
            "wild rye", "wild rye grass",
        },
        "HELIANTHUS TUBEROSUS": {
            "wild artichoke", "wild artichokes", "jerusalem artichoke",
        },
        "AGASTACHE SP.": {
            "hyssop", "hysop", "giant hyssop",
        },
    }.items()
    for raw in raws
}


def write_graph(driver, chunk_id: str, date: str, result: ExtractionResult) -> None:
    node_map = {
        node.id: node
        for node in result.nodes
        if node.label in VALID_NODE_LABELS and node.name.strip()
    }

    # Normalize canonicalName to ALL CAPS; fall back to uppercased raw name
    for node in node_map.values():
        node.canonicalName = (node.canonicalName or node.name).strip().upper()
        # If the raw name unambiguously identifies a different person, correct it
        # now so that relationships also land on the right node.
        rerouted = _ALIAS_REROUTE.get(node.name.strip().lower())
        if rerouted and rerouted != node.canonicalName:
            node.canonicalName = rerouted
        elif node.label == "Person" and not _alias_plausible(node.name, node.canonicalName):
            # The LLM assigned this raw name to a completely different Person
            # (e.g. "Sergt. Gass" on John Ordway's node).  Derive a canonical
            # directly from the raw name by stripping title tokens so the node
            # lands on the right person — or at least a stub the disambiguator
            # can merge — rather than being silently dropped from the wrong one.
            cleaned = re.sub(r"[^a-z0-9\s]", "", node.name.lower())
            fallback_toks = [t for t in cleaned.split()
                             if t not in _TITLE_TOKENS and len(t) >= 2]
            fallback = " ".join(fallback_toks).upper()
            if fallback and fallback != node.canonicalName:
                node.canonicalName = fallback

    PLACEHOLDER_CANONICAL = {"UNKNOWN", "UNKNOWN SP.", "N/A", "NA", "NONE", ""} | set(VALID_NODE_LABELS) | {l.upper() for l in VALID_NODE_LABELS}
    node_map = {
        nid: node for nid, node in node_map.items()
        if node.canonicalName not in PLACEHOLDER_CANONICAL
        and not _is_bare_initial(node.canonicalName)
    }

    with driver.session() as s:
        for node in node_map.values():
            # Merge on canonicalName (stable key); set display name on first creation
            display_name = node.canonicalName.title()
            s.run(
                f"MERGE (n:{node.label} {{canonicalName: $cn}})"
                " ON CREATE SET n.name = $displayName, n.aliases = []",
                cn=node.canonicalName, displayName=display_name,
            )
            if node.label in SPECIES_LABELS:
                s.run(
                    f"MATCH (n:{node.label} {{canonicalName: $cn}}) SET n:Species",
                    cn=node.canonicalName,
                )
            # Append raw text form to aliases for provenance, skipping it when
            # the LLM echoed the canonical name back as the raw name, or when the
            # raw text is a compound reference (contains "&") that the LLM failed
            # to split into separate nodes despite the extraction rules, or a
            # completely different name that the LLM mis-assigned to this node
            # (e.g. "Sergt. Gass" or "Mr. Durion" landing on John Ordway).
            # Exception: names that arrived via _ALIAS_REROUTE are human-curated
            # and always valid aliases for their canonical — skip _alias_plausible
            # for them so that forms like "drewyer" → GEORGE DROUILLARD and
            # "sah-ca-gar-we-ah" → SACAGAWEA are preserved in the aliases list
            # and therefore reachable via the full-text index.
            raw = node.name.strip()
            reroute_confirmed = _ALIAS_REROUTE.get(raw.lower()) == node.canonicalName
            if (raw and raw != display_name and raw.upper() != node.canonicalName
                    and "&" not in raw
                    and (reroute_confirmed or _alias_plausible(raw, node.canonicalName))):
                s.run(
                    f"MATCH (n:{node.label} {{canonicalName: $cn}})"
                    " WHERE NOT $raw IN coalesce(n.aliases, [])"
                    " SET n.aliases = coalesce(n.aliases, []) + [$raw]",
                    cn=node.canonicalName, raw=raw,
                )

        # Merge relationships between entities
        for rel in result.relationships:
            start = node_map.get(rel.start_node_id)
            end   = node_map.get(rel.end_node_id)
            if not start or not end or rel.type not in VALID_REL_TYPES:
                continue
            allowed = REL_SCHEMA.get(rel.type)
            if allowed:
                start_ok, end_ok = allowed
                if start.label not in start_ok or end.label not in end_ok:
                    continue
            s.run(
                f"MATCH (a:{start.label} {{canonicalName: $scn}})"
                f" MATCH (b:{end.label} {{canonicalName: $ecn}})"
                f" MERGE (a)-[r:{rel.type} {{chunkId: $cid}}]->(b)"
                " ON CREATE SET r.date = $date",
                scn=start.canonicalName, ecn=end.canonicalName,
                cid=chunk_id, date=date,
            )

        # Link entities back to their source chunk
        for node in node_map.values():
            s.run(
                f"MATCH (e:{node.label} {{canonicalName: $cn}})"
                f" MATCH (c:Chunk {{chunkId: $cid}})"
                f" MERGE (e)-[:MENTIONED_IN]->(c)",
                cn=node.canonicalName, cid=chunk_id,
            )

        # Mark chunk as processed
        s.run(
            "MATCH (c:Chunk {chunkId: $cid}) SET c.graphExtracted = true",
            cid=chunk_id,
        )

# ── Extraction ────────────────────────────────────────────────────────────────

async def extract_chunk(
    client: AsyncOpenAI,
    driver,
    chunk_id: str,
    date: str,
    text: str,
    semaphore: asyncio.Semaphore,
    idx: int,
    total: int,
) -> None:
    async with semaphore:
        try:
            response = await client.beta.chat.completions.parse(
                model=EXTRACTION_MODEL,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                response_format=ExtractionResult,
            )
            result = response.choices[0].message.parsed
            if result:
                write_graph(driver, chunk_id, date, result)
                print(
                    f"  [{idx:>4}/{total}] ok  {chunk_id}"
                    f"  {len(result.nodes)} nodes  {len(result.relationships)} rels"
                )
            else:
                print(f"  [{idx:>4}/{total}] empty parse  {chunk_id}")
        except Exception as exc:
            print(f"  [{idx:>4}/{total}] FAILED  {chunk_id}: {exc}")


async def run_extraction(driver) -> None:
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    setup_constraints(driver)

    with driver.session() as s:
        rows = s.run("""
            MATCH (c:Chunk)
            WHERE c.graphExtracted IS NULL OR c.graphExtracted = false
            RETURN c.chunkId AS chunkId, c.date AS date, c.text AS text
            ORDER BY c.sequence
        """).data()

    if not rows:
        print("No unprocessed chunks — nothing to do.")
        return

    total = len(rows)
    print(f"Extracting from {total} chunks  model={EXTRACTION_MODEL}  concurrency={CONCURRENCY}")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*[
        extract_chunk(client, driver, r["chunkId"], r["date"], r["text"], semaphore, i + 1, total)
        for i, r in enumerate(rows)
    ])

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")
    asyncio.run(run_extraction(driver))
    driver.close()
    print("\nExtraction complete.")


if __name__ == "__main__":
    main()
