# Demo Questions — GraphRAG vs. Vector RAG

Questions where the knowledge graph gives a decisive advantage over chunk
similarity alone. Grouped by the graph capability being demonstrated.

---

## Multi-hop traversal

1. **Which Native nations did Sacagawea help the expedition communicate with?**
   *Graph: Person → INTERPRETED_FOR → NativeNation. Vector RAG must find every
   chunk mentioning Sacagawea interpreting and piece together the nations — easy
   to miss nations that appear only once.*

2. **What was the chain of guides that led the expedition through the Rocky Mountains?**
   *Graph: traverse GUIDED relationships across multiple people and legs of the
   journey. Vector RAG can surface individual mentions but not the full relay.*

3. **Which expedition members were present at the death of Sergeant Charles Floyd?**
   *Graph: Event node linked to Person nodes via co-occurrence in the same chunks.
   Vector RAG may find the death entry but not reliably enumerate everyone present.*

---

## Aggregation across the full corpus

4. **How many distinct Native nations did the expedition encounter?**
   *Graph: count NativeNation nodes that have a MET_WITH edge to an expedition
   member. Vector RAG has no way to count — it retrieves chunks, not a census.*

5. **List every species that Meriwether Lewis observed or described in the journals.**
   *Graph: Person → OBSERVED → Species. A complete, deduplicated list is a
   single graph traversal; vector RAG returns the most similar chunks, leaving
   many observations buried.*

6. **Which members of the Corps of Discovery are mentioned in the journals?**
   *Graph: Person → MEMBER_OF → Corps. Vector RAG requires the right chunks to
   surface; obscure members mentioned only once are easily missed.*

7. **What trade goods did the expedition use when dealing with Native nations?**
   *Graph: Supply nodes connected via TRADED_WITH. Vector RAG struggles to
   aggregate a complete inventory across hundreds of separate trading events.*

---

## Entity-centric queries spanning many chunks

8. **Where did the expedition camp along the Missouri River?**
   *Graph: Place nodes with CAMPED_AT edges filtered by proximity to the Missouri
   River WaterBody. Vector RAG retrieves "camping" chunks but loses the spatial
   structure.*

9. **What rivers did the expedition travel on during the return journey?**
   *Graph: WaterBody nodes with VISITED edges, ordered by chunk sequence. Vector
   RAG has no sense of temporal ordering or direction of travel.*

10. **What provisions did the expedition acquire from the Shoshone?**
    *Graph: NativeNation → ACQUIRED_PROVISION → Supply nodes. Vector RAG conflates
    provisioning events with other Shoshone mentions.*

---

## Comparison across entities

11. **Which expedition member encountered the greatest number of Native nations?**
    *Graph: count distinct NativeNation nodes per Person via MET_WITH. Impossible
    to answer with vector RAG without reading every chunk.*

12. **Which Native nation did the expedition trade with most frequently?**
    *Graph: count TRADED_WITH edges per NativeNation. Vector RAG cannot count
    relationship frequency.*

13. **Who observed the most animal species during the expedition?**
    *Graph: count Species nodes per Person via OBSERVED. Vector RAG has no
    structured way to tally observations by person.*

---

## Relationship path and provenance

14. **How did the expedition come to acquire horses from the Shoshone?**
    *Graph: follow the chain — Sacagawea (MEMBER_OF Shoshone, INTERPRETED_FOR
    expedition) → MET_WITH Shoshone chief → TRADED_WITH → Supply (horses).
    Vector RAG surfaces horse-related chunks but not the relational path.*

15. **Which geographic features did Lewis and Clark name, and where are they?**
    *Graph: Person → NAMED → Place or WaterBody, with the location as a node
    attribute. Vector RAG returns naming passages but not a clean enumeration.*

16. **Who interpreted for the Nez Perce, and what did the expedition trade with them?**
    *Graph: two hops — Person → INTERPRETED_FOR → Nez Perce, then Nez Perce →
    TRADED_WITH → Supply. Vector RAG is unlikely to answer both parts accurately
    from a single retrieval.*

---

## Network and community questions

17. **Which Native nations did the expedition meet after crossing the Continental Divide?**
    *Graph: filter MET_WITH edges by chunk sequence number (post-crossing). Vector
    RAG cannot filter by temporal position in the journey.*

18. **Which locations appear in the journals as both a camping spot and a trading location?**
    *Graph: Place nodes with both CAMPED_AT and TRADED_WITH edges. Vector RAG
    would need separate retrievals and manual intersection.*

19. **What events took place at Fort Mandan?**
    *Graph: Event nodes co-located with Fort Mandan Place node. Vector RAG
    returns Fort Mandan chunks but may miss events described only tangentially.*

---

## Alias / disambiguation showcase

20. **What role did the Minnetarees play in the expedition's winter of 1804–1805?**
    *Graph: "Minnetarees" is an alias for the Hidatsa node — the query resolves
    correctly regardless of which spelling appears in a chunk. Vector RAG treats
    "Minnetarees" and "Hidatsa" as different topics and returns inconsistent
    results depending on which spelling appears in the nearest chunks.*
