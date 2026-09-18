# Data source

Raw dataset is not committed (84.6GB, gitignored). To reproduce locally:

1. Download the export (Shodan-style internet scan snapshot, JSON Lines,
   zstd-compressed) and place it at the repo root as `2026-09-14T10-00-00.json.zst`.
2. Run `python scripts/decompress_source.py` to produce
   `2026-09-14T10-00-00.json` (84.6GB).
3. Run the Bronze ingestion: `python pipeline/bronze/ingest.py`.

## Format

JSON Lines, one record per exposed service/banner observation
(`ip`, `port`, `timestamp` grain), ~4.2M rows. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the pipeline that turns this into a
company-level, scored account list.
