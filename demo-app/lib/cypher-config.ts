/**
 * Text2Cypher configuration for the Lewis & Clark knowledge graph.
 *
 * GRAPH_SCHEMA  — describes every node label, property, and relationship type
 *                 so the LLM knows what it can query.
 *
 * FEW_SHOT      — example question → Cypher pairs. Add more here to improve
 *                 accuracy on new question patterns without touching agent logic.
 */

export const GRAPH_SCHEMA = `
Neo4j knowledge graph extracted from the Lewis & Clark Expedition journals (1804-1806).

NODE TYPES (all have a canonicalName property unless noted):
  Person          — expedition members, Native Americans, other individuals
  Place           — named locations on land  (filter: WHERE NOT node:GenericLocation)
  WaterBody       — rivers, lakes, creeks    (filter: WHERE NOT node:GenericLocation)
  AnimalSpecies   — animals observed or described
  PlantSpecies    — plants observed or collected
  NativeNation    — Indigenous nations encountered
  Taxon           — taxonomy node; properties: rank (genus|family|order|class|phylum|kingdom), name
                    NOTE: Taxon uses .name, NOT .canonicalName
  Chunk           — journal passage; properties: text, date, author, chunkId

RELATIONSHIP TYPES:
  Activity relationships — all carry {date, chunkId} properties:
  (Person)-[:OBSERVED {date, chunkId}]->(AnimalSpecies|PlantSpecies|Place|WaterBody)
  (Person)-[:CAMPED_AT {date, chunkId}]->(Place|WaterBody)
  (Person)-[:VISITED  {date, chunkId}]->(Place|WaterBody)
  (Person|NativeNation)-[:MET_WITH   {date, chunkId}]->(Person|NativeNation)
  (Person|NativeNation)-[:TRADED_WITH {date, chunkId}]->(Person|NativeNation)
  (Person|NativeNation)-[:ACQUIRED_PROVISION {date, chunkId}]->(Supply)
  (Person)-[:GUIDED    {date, chunkId}]->(Person)
  (Person)-[:MEMBER_OF {date, chunkId}]->(NativeNation)
  (Person)-[:NAMED     {date, chunkId}]->(Place|WaterBody)

  Taxonomy / structure (no date):
  (AnimalSpecies|PlantSpecies)-[:BELONGS_TO]->(Taxon)
  (Taxon)-[:BELONGS_TO]->(Taxon)

  Corpus links (no date):
  (AnyEntity)-[:MENTIONED_IN]->(Chunk)
  (Chunk)-[:NEXT_CHUNK]->(Chunk)   — earlier-to-later chronological order

IMPORTANT — use relationship dates for temporal queries:
  Prefer r.date over joining through MENTIONED_IN chunks.
  r.date is the date of the journal entry the relationship was extracted from,
  making it the most precise signal for when an activity actually occurred.
  Example: MATCH (p:Person)-[r:CAMPED_AT]->(place) WHERE r.date >= date($start)
`.trim();

export const FEW_SHOT = `
Q: Which Native Nations did the expedition trade with?
MATCH (p:Person)-[:TRADED_WITH]->(n:NativeNation)
RETURN DISTINCT n.canonicalName AS nation
ORDER BY nation

Q: What species did Lewis observe near the Columbia River?
// $person = entity param (Person resolved from "lewis" in question)
// $waterBody = entity param (WaterBody resolved from "columbia" in question)
MATCH (p:Person {canonicalName: $person})-[:OBSERVED]->(s)
WHERE (s:AnimalSpecies OR s:PlantSpecies)
MATCH (s)-[:MENTIONED_IN]->(c:Chunk)
MATCH (w:WaterBody {canonicalName: $waterBody})-[:MENTIONED_IN]->(c)
RETURN DISTINCT s.canonicalName AS species, head(labels(s)) AS type
ORDER BY species
LIMIT 30

Q: What animal species did Meriwether Lewis observe?
// $person = entity param (Person resolved from "lewis" in question)
MATCH (p:Person {canonicalName: $person})-[:OBSERVED]->(s:AnimalSpecies)
RETURN DISTINCT s.canonicalName AS species
ORDER BY species

Q: What bird families did the expedition encounter?
MATCH (p:Person)-[:OBSERVED]->(s:AnimalSpecies)-[:BELONGS_TO*]->(f:Taxon)
WHERE f.rank = 'family'
RETURN DISTINCT f.name AS family, count(DISTINCT s) AS species_count
ORDER BY species_count DESC

Q: Which expedition members met with Native Nations, and which nations?
MATCH (p:Person)-[:MET_WITH]->(n:NativeNation)
RETURN p.canonicalName AS person, collect(DISTINCT n.canonicalName) AS nations
ORDER BY person

Q: What places did the corps camp at?
MATCH (p:Person)-[r:CAMPED_AT]->(place)
WHERE (place:Place OR place:WaterBody) AND NOT place:GenericLocation
RETURN DISTINCT place.canonicalName AS place
ORDER BY place.canonicalName
LIMIT 25

Q: What plant species were observed in the journals?
MATCH (p:Person)-[:OBSERVED]->(s:PlantSpecies)
RETURN DISTINCT s.canonicalName AS species
ORDER BY species

Q: Which species were most frequently mentioned?
MATCH (s)-[:MENTIONED_IN]->(c:Chunk)
WHERE s:AnimalSpecies OR s:PlantSpecies
RETURN s.canonicalName AS species, head(labels(s)) AS type, count(c) AS mentions
ORDER BY mentions DESC
LIMIT 10

Q: What is the full taxonomy of the grizzly bear?
MATCH (s:AnimalSpecies)-[:BELONGS_TO*]->(ancestor:Taxon)
WHERE s.canonicalName CONTAINS 'GRIZZLY'
RETURN s.canonicalName AS species,
       [x IN collect(ancestor) | x.rank + ': ' + x.name] AS taxonomy

Q: Which rivers did the corps travel along?
MATCH (p:Person)-[:VISITED|CAMPED_AT]->(w:WaterBody)
WHERE NOT w:GenericLocation
RETURN DISTINCT w.canonicalName AS waterBody
ORDER BY waterBody
LIMIT 20

Q: How many distinct animal species did the expedition observe?
MATCH (p:Person)-[:OBSERVED]->(s:AnimalSpecies)
RETURN count(DISTINCT s) AS totalSpecies

Q: What places did the corps visit before Sergeant Floyd died, and when?
// $event = Event anchor (e.g. DEATH OF SERGT. FLOYD)
// Use r.date (the relationship's own date) — avoids false positives from places
// mentioned in passing rather than actually visited at that time.
MATCH (e:Event {canonicalName: $event})-[:MENTIONED_IN]->(ec:Chunk)
WITH min(ec.date) AS eventDate
MATCH (p:Person)-[r:VISITED|CAMPED_AT]->(place)
WHERE (place:Place OR place:WaterBody) AND NOT place:GenericLocation
  AND r.date < eventDate
RETURN DISTINCT place.canonicalName AS place,
       min(toString(r.date)) AS visit_date
ORDER BY visit_date
LIMIT 15

Q: What places did the corps visit after leaving Fort Mandan, with dates?
// $anchorDate injected from vector search (corpus date near Fort Mandan departure)
MATCH (p:Person)-[r:VISITED|CAMPED_AT]->(place)
WHERE (place:Place OR place:WaterBody) AND NOT place:GenericLocation
  AND r.date > date($anchorDate)
RETURN DISTINCT place.canonicalName AS place,
       min(toString(r.date)) AS visit_date
ORDER BY visit_date
LIMIT 15

Q: What places did the corps visit in the two months before the birth of a child?
// $event = Event anchor (e.g. BIRTH OF CHARBONNEAU'S SON or BIRTH OF CHILD)
MATCH (e:Event {canonicalName: $event})-[:MENTIONED_IN]->(ec:Chunk)
WITH min(ec.date) AS eventDate
MATCH (p:Person)-[r:VISITED|CAMPED_AT]->(place)
WHERE (place:Place OR place:WaterBody) AND NOT place:GenericLocation
  AND r.date >= eventDate - duration({months: 2})
  AND r.date < eventDate
RETURN DISTINCT place.canonicalName AS place,
       min(toString(r.date)) AS visit_date
ORDER BY visit_date
LIMIT 15

Q: What species were observed in the week after a significant event?
// $event = Event anchor resolved from the question
MATCH (e:Event {canonicalName: $event})-[:MENTIONED_IN]->(ec:Chunk)
WITH min(ec.date) AS eventDate
MATCH (p:Person)-[r:OBSERVED]->(s)
WHERE (s:AnimalSpecies OR s:PlantSpecies)
  AND r.date > eventDate
  AND r.date <= eventDate + duration({days: 7})
RETURN DISTINCT s.canonicalName AS species, head(labels(s)) AS type,
       min(toString(r.date)) AS observed_date
ORDER BY observed_date
LIMIT 30

Q: What are the different names or spellings for George Drouillard in the journals?
// $person = entity param (Person resolved from "drouillard" in question)
MATCH (p:Person {canonicalName: $person})
RETURN p.canonicalName AS canonicalName, p.aliases AS knownAs

Q: What conifer or evergreen tree species did the expedition observe?
// $taxon = taxonomic group anchor (e.g. Pinaceae, Cupressaceae) resolved from "conifer" / "evergreen"
// Use BELONGS_TO* to find all PlantSpecies in that taxonomic group, then check they were observed
MATCH (s:PlantSpecies)-[:BELONGS_TO*]->(t:Taxon {name: $taxon})
MATCH (p:Person)-[:OBSERVED]->(s)
RETURN DISTINCT s.canonicalName AS species
ORDER BY species

Q: What rodent species were mentioned before they reached the Rocky Mountains?
// $taxon  = Taxon anchor for rodents (Rodentia order)
// $place  = Place anchor — use "Gates of the Rocky Mountains" as the entry-point
//           landmark; its first mention date serves as the cutoff
MATCH (loc:Place {canonicalName: $place})-[:MENTIONED_IN]->(c:Chunk)
WITH min(c.date) AS cutoffDate
MATCH (s:AnimalSpecies)-[:BELONGS_TO*]->(t:Taxon {name: $taxon})
MATCH (p:Person)-[r:OBSERVED]->(s)
WHERE r.date < cutoffDate
RETURN DISTINCT s.canonicalName AS species, min(toString(r.date)) AS first_observed
ORDER BY first_observed
LIMIT 20

Q: Were grizzly bears or salmon mentioned more often in the journals?
// $animal = specific species anchor  (e.g. URSUS ARCTOS HORRIBILIS for grizzly bear)
// $taxon  = taxonomic group anchor   (e.g. Salmonidae or Oncorhynchus for salmon)
//
// Rule: use $animal / $plant for a *specific* named species; use $taxon with
// BELONGS_TO* for a *category* of species (salmon, bears, birds, etc.)
MATCH (bear:AnimalSpecies {canonicalName: $animal})-[:MENTIONED_IN]->(c1:Chunk)
WITH count(DISTINCT c1) AS bearMentions
MATCH (s:AnimalSpecies)-[:BELONGS_TO*]->(t:Taxon {name: $taxon})
MATCH (s)-[:MENTIONED_IN]->(c2:Chunk)
RETURN bearMentions AS grizzly_bear_mentions, count(DISTINCT c2) AS salmon_mentions

Q: Which Native Nations did Sacagawea interpret for?
// $person = entity param (Person resolved from "sacagawea")
// Sacagawea used many name variants in the journals; her Person node is linked to
// relevant passages via the Sacagawea enrichment pipeline even when she is not
// named directly. INTERPRETED_FOR captures her role as language intermediary.
MATCH (p:Person {canonicalName: $person})-[:INTERPRETED_FOR]->(n:NativeNation)
RETURN DISTINCT n.canonicalName AS nation
ORDER BY nation

`.trim();
