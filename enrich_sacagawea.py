#!/usr/bin/env python3
"""
Sacagawea reference enrichment.

Scrapes the curated list of journal entries that reference Sacagawea by
indirect terms ("the squaw", "the interpreter's wife", "the Snake woman",
etc.) from lewis-clark.org, then matches each entry to a Chunk node by:
  1. Date — fetch only chunks from that journal date
  2. String similarity — pick the chunk whose text best overlaps the quote
     using difflib SequenceMatcher (longest contiguous match / quote length),
     which is robust to edition differences and OCR variation.

Also adds all indirect surface forms to Sacagawea's aliases list.

Source: https://lewis-clark.org/people/sacagawea/sacagawea-in-the-journals/
"""

import datetime
import os
import re
import subprocess
from difflib import SequenceMatcher

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

SOURCE_URL   = "https://lewis-clark.org/people/sacagawea/sacagawea-in-the-journals/"
SACAGAWEA_CN = "SACAGAWEA"
MIN_SCORE    = 0.25   # minimum matched-chars fraction to accept a chunk

# Short indirect forms used in the journals to refer to Sacagawea.
# Added to her aliases so full-text search resolves them.
SACAGAWEA_ALIASES = [
    "the squaw", "the squar", "the squar interpretress", "the Snake woman",
    "the Indian woman", "the interpreter's wife", "the interpreters wife",
    "Janey", "Shabono's wife", "Charbono's wife", "the Snake Indian wife",
    "our interpretress", "our interpetr wife",
]


# ── Scraping ──────────────────────────────────────────────────────────────────

def scrape_entries() -> list[dict]:
    """
    Fetch the lewis-clark.org page and return a list of entries, each with:
      title  — section heading
      date   — datetime.date parsed from the day-by-day URL slug, or None
      quotes — list of (text, diarist) tuples from blockquotes
    """
    result = subprocess.run(
        [
            "curl", "-s", SOURCE_URL,
            "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "-H", "Accept: text/html",
            "-H", "Accept-Language: en-US,en;q=0.9",
        ],
        capture_output=True, text=True, check=True,
    )
    soup = BeautifulSoup(result.stdout, "html.parser")

    entries = []
    current: dict | None = None
    last_date: datetime.date | None = None

    for tag in soup.find_all(["h3", "p", "blockquote"]):
        if tag.name == "h3":
            if current:
                if current["date"] is None:
                    current["date"] = last_date
                entries.append(current)
            current = {"title": tag.get_text(strip=True), "date": None, "quotes": []}

        elif tag.name == "p" and current:
            # Try day-by-day link first
            for a in tag.find_all("a", href=True):
                m = re.search(r"/day-by-day/([^/]+)/", a["href"])
                if m and current["date"] is None:
                    current["date"] = _parse_slug(m.group(1))
                    break
            # Fallback: plain-text date like "June 16, 1805"
            if current["date"] is None:
                m = re.search(r"([A-Za-z]+ \d{1,2},\s*\d{4})", tag.get_text())
                if m:
                    current["date"] = _parse_text_date(m.group(1))
            # Track the last successfully parsed date for date-less entries
            if current["date"] is not None:
                last_date = current["date"]

        elif tag.name == "blockquote" and current:
            raw = tag.get_text(separator=" ", strip=True)
            if "—" in raw:
                parts = raw.rsplit("—", 1)
                quote_text = parts[0].strip()
                diarist    = parts[1].strip()
            else:
                quote_text = raw
                diarist    = "unknown"
            if quote_text:
                current["quotes"].append((quote_text, diarist))

    if current:
        if current["date"] is None:
            current["date"] = last_date
        entries.append(current)

    return entries


def _parse_slug(slug: str) -> datetime.date | None:
    """Parse a day-by-day slug like '4-nov-1804' into a date."""
    try:
        return datetime.datetime.strptime(slug, "%d-%b-%Y").date()
    except ValueError:
        return None


def _parse_text_date(text: str) -> datetime.date | None:
    """Parse a plain-text date like 'June 16, 1805' into a date."""
    try:
        return datetime.datetime.strptime(text.strip(), "%B %d, %Y").date()
    except ValueError:
        return None


# ── Chunk matching ────────────────────────────────────────────────────────────

def chunks_for_date(driver, date: datetime.date) -> list[tuple[str, str]]:
    """Return (elementId, text) for all chunks on the given date."""
    with driver.session() as s:
        result = s.run(
            "MATCH (c:Chunk) WHERE c.date = $date RETURN elementId(c) AS eid, c.text AS text",
            date=date,
        )
        return [(r["eid"], r["text"]) for r in result]


def _norm(text: str) -> str:
    """Lowercase and collapse punctuation/whitespace for fuzzy comparison."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def best_chunk(quote: str, candidates: list[tuple[str, str]]) -> tuple[str, float] | None:
    """
    Return (elementId, score) of the chunk whose text best overlaps the quote,
    or None if no candidate clears MIN_SCORE.

    Score = sum of all matching character blocks / len(normalized quote).
    Summing all blocks (rather than just the longest) handles the case where
    the two editions agree on most words but differ in spelling or punctuation
    throughout, so no single long contiguous run emerges.
    """
    q = _norm(quote)
    if not q:
        return None
    best_eid, best_score = None, 0.0
    for eid, text in candidates:
        sm     = SequenceMatcher(None, q, _norm(text), autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())
        score   = matched / len(q)
        if score > best_score:
            best_score, best_eid = score, eid
    if best_score >= MIN_SCORE:
        return best_eid, best_score
    return None


# ── Neo4j writes ──────────────────────────────────────────────────────────────

def ensure_sacagawea(driver) -> None:
    """Create the Sacagawea Person node if it doesn't already exist."""
    with driver.session() as s:
        s.run(
            """
            MERGE (saca:Person {canonicalName: $cn})
            ON CREATE SET saca.name    = 'Sacagawea',
                          saca.aliases = []
            """,
            cn=SACAGAWEA_CN,
        )


def link_chunk(driver, eid: str) -> None:
    """MERGE a MENTIONED_IN relationship from Sacagawea to the given chunk."""
    with driver.session() as s:
        s.run(
            """
            MATCH (saca:Person {canonicalName: $cn})
            MATCH (c:Chunk) WHERE elementId(c) = $eid
            MERGE (saca)-[:MENTIONED_IN]->(c)
            """,
            cn=SACAGAWEA_CN, eid=eid,
        )


def update_aliases(driver, surface_forms: list[str]) -> None:
    """Merge surface forms into Sacagawea's aliases list."""
    with driver.session() as s:
        s.run(
            """
            MATCH (saca:Person {canonicalName: $cn})
            SET saca.aliases = [x IN coll.distinct(
                coalesce(saca.aliases, []) + $forms
            ) WHERE x <> saca.name AND x <> saca.canonicalName]
            """,
            cn=SACAGAWEA_CN,
            forms=surface_forms,
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")

    ensure_sacagawea(driver)
    print("Ensured Sacagawea node exists.")

    print(f"Fetching {SOURCE_URL} ...")
    entries = scrape_entries()
    print(f"Scraped {len(entries)} entries.\n")

    linked = skipped = 0

    for entry in entries:
        title = entry["title"]
        date  = entry["date"]

        if date is None:
            print(f"  {'(no date)':20s}  {title[:45]}  → skipped (no date parsed)")
            skipped += 1
            continue

        candidates = chunks_for_date(driver, date)
        if not candidates:
            print(f"  {str(date):20s}  {title[:45]}  → no chunks for date")
            skipped += 1
            continue

        for quote_text, _diarist in entry["quotes"]:
            hit = best_chunk(quote_text, candidates)
            if hit:
                eid, score = hit
                link_chunk(driver, eid)
                linked += 1
                print(f"  {str(date):20s}  {title[:45]}  → linked (score {score:.2f})")
            else:
                print(f"  {str(date):20s}  {title[:45]}  → no match above threshold")
                skipped += 1

    print(f"\nUpdating Sacagawea aliases ...")
    update_aliases(driver, SACAGAWEA_ALIASES)

    driver.close()
    print(f"\nDone. {linked} chunks linked, {skipped} entries skipped.")


if __name__ == "__main__":
    main()
