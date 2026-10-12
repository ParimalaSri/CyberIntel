from pathlib import Path

SOURCE = Path(r"D:\Cyber_DataSet\2026-09-14T10-00-00.json")
SAMPLE_JSON = Path(r"D:\Cyber_DataSet\data\samples\hosts_sample.json")

SAMPLE_JSON.parent.mkdir(parents=True, exist_ok=True)

with open(SOURCE, "r", encoding="utf-8") as src, \
     open(SAMPLE_JSON, "w", encoding="utf-8") as dst:

    for i, line in enumerate(src):
        if i >= 5000:
            break
        dst.write(line)

print(f"Created JSON sample: {SAMPLE_JSON}")
