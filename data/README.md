# data/

This directory is a placeholder for the Neo4j database dump. The dump file is too large for Git, so it is distributed as a **GitHub Release asset**.

## Download

Grab `lewis-clark-graphrag.dump` from the [latest release](../../releases/latest) and place it in this directory.

```bash
# Example using GitHub CLI
gh release download --pattern "lewis-clark-graphrag.dump" --dir data/
```

## Restoring the dump

### Neo4j Desktop

1. Stop your DBMS.
2. Open the DBMS menu → **Manage** → **…** (three-dot menu on the database) → **Load**.
3. Select `lewis-clark-graphrag.dump` and confirm.
4. Start the DBMS.

### neo4j-admin (CLI)

```bash
neo4j-admin database load --from-path=/path/to/lewis-clark-graphrag.dump --overwrite-destination=true neo4j
```

Run this while the database is stopped, then start Neo4j.

### Neo4j Aura

Aura Free does not support the `database load` command. Use the full ingest pipeline instead (`python build_graph.py`), or upgrade to an Aura tier that supports import.

## After restoring

The dump includes all embeddings, full-text indexes, and entity nodes — everything the demo app needs. Follow the demo app setup in [`demo-app/README.md`](../demo-app/README.md) and skip straight to `npm run dev`.
