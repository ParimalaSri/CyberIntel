"""Record source-file identity so the pipeline is reproducible without
duplicating the 84.6GB raw JSONL into Parquet (see docs/ARCHITECTURE.md)."""
import json
import os
import sys
import time

SOURCE = r"D:\Cyber_DataSet\2026-09-14T10-00-00.json"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "bronze", "manifest.json")

AVG_LINE_BYTES = 20155  # from random-sample estimation, see chat history / ARCHITECTURE.md


def main():
    if not os.path.exists(SOURCE):
        print(f"Source not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)

    stat = os.stat(SOURCE)
    manifest = {
        "source_path": SOURCE,
        "size_bytes": stat.st_size,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "estimated_row_count": int(stat.st_size / AVG_LINE_BYTES),
        "format": "json_lines",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest: {OUT}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
