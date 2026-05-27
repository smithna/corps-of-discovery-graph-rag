#!/usr/bin/env python3
"""
Lewis & Clark Journals — Neo4j ingestion pipeline.

Fetches the Project Gutenberg HTML, chunks by journal entry (splitting long
entries at paragraph/sentence boundaries), embeds with OpenAI, and loads
Chunk nodes + NEXT_CHUNK relationships into Neo4j with a vector index.
"""

import datetime
import hashlib
import os
import re
import time
from dataclasses import dataclass, field

import requests
import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

GUTENBERG_URL = "https://www.gutenberg.org/files/8419/8419-h/8419-h.htm"

NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USER     = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

EMBEDDING_MODEL  = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM    = int(os.getenv("EMBEDDING_DIM", "1536"))
MAX_CHUNK_TOKENS = int(os.getenv("MAX_CHUNK_TOKENS", "500"))
EMBED_BATCH_SIZE = 100
LOAD_BATCH_SIZE  = 100

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id:    str
    text:        str
    author:      str
    date_str:    str           # "May 14, 1804" — kept for chunk_id hashing
    date:        datetime.date # parsed Neo4j-compatible date
    sequence:    int           # global document order, used for NEXT_CHUNK
    token_count: int
    embedding:   list[float] = field(default_factory=list)


# ── Fetch & parse ─────────────────────────────────────────────────────────────

# Matches headers like:
#   [Clark, May 14, 1804]
#   [Lewis and Clark, June 3, 1804]
#   [Clark and Whitehouse, September 9, 1804]
HEADER_RE = re.compile(
    r'^\s*\[([A-Za-z]+(?:\s+and\s+[A-Za-z]+)?)'   # author(s)
    r',\s+'
    r'([A-Za-z]+\s+\d{1,2},\s+\d{4})\]\s*$',       # "Month D, YYYY"
    re.MULTILINE,
)


def fetch_entries(url: str) -> list[tuple[str, str, str]]:
    """
    Download the Gutenberg HTML and return (author, date_str, text) tuples,
    one per journal entry header found in the document.
    """
    print(f"Fetching {url} ...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Walk <h2> and <p> tags in document order.
    # Headers are <h2> tags; body is <p> tags (skipping class="toc" entries,
    # which are a table-of-contents at the top of the Gutenberg file and would
    # otherwise match HEADER_RE and assign all body text to the last TOC date).
    entries: list[tuple[str, str, str]] = []
    current_author: str | None = None
    current_date:   str | None = None
    current_parts:  list[str]  = []

    for tag in soup.find_all(["h2", "p"]):
        if "toc" in (tag.get("class") or []):
            continue
        text = tag.get_text(separator=" ", strip=True)
        if not text:
            continue
        m = HEADER_RE.match(text)
        if m:
            # Flush the previous entry
            if current_author and current_parts:
                body = "\n\n".join(current_parts).strip()
                if body:
                    entries.append((current_author, current_date, body))
            current_author = m.group(1).strip()
            current_date   = m.group(2).strip()
            current_parts  = []
        elif current_author and tag.name == "p":
            current_parts.append(text)

    # Flush the final entry
    if current_author and current_parts:
        body = "\n\n".join(current_parts).strip()
        if body:
            entries.append((current_author, current_date, body))

    print(f"Parsed {len(entries)} journal entries.")
    return entries


# ── Chunking ──────────────────────────────────────────────────────────────────

def _encoder():
    # cl100k_base is used by all text-embedding-3-* and ada-002 models
    return tiktoken.get_encoding("cl100k_base")


def _count(text: str, encoder) -> int:
    return len(encoder.encode(text))


def _split_long_text(text: str, encoder, max_tokens: int) -> list[str]:
    """
    Greedily pack paragraphs into chunks up to max_tokens.
    Falls back to sentence splitting for paragraphs that are themselves too long.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf:    list[str] = []
    buf_tokens = 0

    def flush():
        nonlocal buf, buf_tokens
        if buf:
            chunks.append("\n\n".join(buf))
            buf, buf_tokens = [], 0

    for para in paragraphs:
        pt = _count(para, encoder)

        if pt > max_tokens:
            flush()
            # Split at sentence boundaries
            sentences = re.split(r"(?<=[.!?])\s+", para)
            sbuf: list[str] = []
            stokens = 0
            for sent in sentences:
                st = _count(sent, encoder)
                if stokens + st > max_tokens and sbuf:
                    chunks.append(" ".join(sbuf))
                    sbuf, stokens = [], 0
                sbuf.append(sent)
                stokens += st
            if sbuf:
                chunks.append(" ".join(sbuf))

        elif buf_tokens + pt > max_tokens:
            flush()
            buf, buf_tokens = [para], pt

        else:
            buf.append(para)
            buf_tokens += pt

    flush()
    return chunks or [text]


def build_chunks(
    entries: list[tuple[str, str, str]],
    max_tokens: int,
) -> list[Chunk]:
    encoder  = _encoder()
    chunks:  list[Chunk] = []
    sequence = 0

    for author, date_str, text in entries:
        parsed   = datetime.datetime.strptime(date_str, "%B %d, %Y").date()
        segments = _split_long_text(text, encoder, max_tokens)

        for i, seg in enumerate(segments):
            tc       = _count(seg, encoder)
            # Include segment index so multi-chunk entries get distinct IDs
            chunk_id = hashlib.sha1(
                f"{sequence}|{author}|{date_str}|{i}".encode()
            ).hexdigest()[:16]

            chunks.append(Chunk(
                chunk_id    = chunk_id,
                text        = seg,
                author      = author,
                date_str    = date_str,
                date        = parsed,
                sequence    = sequence,
                token_count = tc,
            ))
            sequence += 1

    print(f"Built {len(chunks)} chunks (max {max_tokens} tokens each).")
    return chunks


# ── Embeddings ────────────────────────────────────────────────────────────────

def embed_chunks(chunks: list[Chunk], client: OpenAI) -> None:
    """Embed all chunks in batches, populating chunk.embedding in place."""
    total = len(chunks)
    print(f"Embedding {total} chunks with {EMBEDDING_MODEL} ...")
    for i in range(0, total, EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        resp  = client.embeddings.create(
            model = EMBEDDING_MODEL,
            input = [c.text for c in batch],
        )
        for chunk, item in zip(batch, resp.data):
            chunk.embedding = item.embedding
        print(f"  {min(i + EMBED_BATCH_SIZE, total)}/{total} embedded")
        if i + EMBED_BATCH_SIZE < total:
            time.sleep(0.25)   # stay comfortably under rate limits


# ── Neo4j ─────────────────────────────────────────────────────────────────────

_SETUP = [
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunkId IS UNIQUE",
    f"""CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS
    FOR (c:Chunk) ON (c.embedding)
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {EMBEDDING_DIM},
        `vector.similarity_function`: 'cosine'
    }}}}""",
]

_MERGE_CHUNKS = """
UNWIND $batch AS row
MERGE (c:Chunk {chunkId: row.chunkId})
SET c.text       = row.text,
    c.author     = row.author,
    c.date       = row.date,
    c.sequence   = row.sequence,
    c.tokenCount = row.tokenCount,
    c.embedding  = row.embedding
"""

_NEXT_CHUNK = """
UNWIND $pairs AS pair
MATCH (a:Chunk {chunkId: pair[0]})
MATCH (b:Chunk {chunkId: pair[1]})
MERGE (a)-[:NEXT_CHUNK]->(b)
"""


def setup_schema(driver) -> None:
    with driver.session() as s:
        for cypher in _SETUP:
            s.run(cypher)
    print("Constraint and vector index ready.")


def load_chunks(driver, chunks: list[Chunk]) -> None:
    total = len(chunks)
    with driver.session() as s:
        for i in range(0, total, LOAD_BATCH_SIZE):
            batch = chunks[i : i + LOAD_BATCH_SIZE]
            s.run(_MERGE_CHUNKS, batch=[
                {
                    "chunkId":    c.chunk_id,
                    "text":       c.text,
                    "author":     c.author,
                    "date":       c.date,
                    "sequence":   c.sequence,
                    "tokenCount": c.token_count,
                    "embedding":  c.embedding,
                }
                for c in batch
            ])
            print(f"  Loaded {min(i + LOAD_BATCH_SIZE, total)}/{total} chunks")
    print("All chunks loaded.")


def link_chunks(driver, chunks: list[Chunk]) -> None:
    pairs = [
        [chunks[i].chunk_id, chunks[i + 1].chunk_id]
        for i in range(len(chunks) - 1)
    ]
    with driver.session() as s:
        for i in range(0, len(pairs), 500):
            s.run(_NEXT_CHUNK, pairs=pairs[i : i + 500])
    print(f"Created {len(pairs)} NEXT_CHUNK relationships.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    oai    = OpenAI(api_key=OPENAI_API_KEY)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected to Neo4j.")

    setup_schema(driver)

    raw     = fetch_entries(GUTENBERG_URL)
    chunks  = build_chunks(raw, MAX_CHUNK_TOKENS)
    embed_chunks(chunks, oai)
    load_chunks(driver, chunks)
    link_chunks(driver, chunks)

    driver.close()
    print("\nIngestion complete.")


if __name__ == "__main__":
    main()
