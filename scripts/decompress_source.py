import zstandard as zstd

input_file = "2026-09-14T10-00-00.json.zst"
output_file = "2026-09-14T10-00-00.json"

with open(input_file, "rb") as compressed:
    dctx = zstd.ZstdDecompressor()
    with open(output_file, "wb") as decompressed:
        dctx.copy_stream(compressed, decompressed)

print("Extraction completed!")