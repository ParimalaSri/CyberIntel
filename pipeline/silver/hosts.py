"""Bronze (raw JSONL) -> Silver Hosts.

Flattens the nested Shodan record into a typed table, one row per
(ip, port, timestamp) observation. Explicit column projection — see
docs/ARCHITECTURE.md "Bronze is the raw JSONL file in place" for why we don't
auto-detect the full 60+-struct source schema.
"""
import os
import time

import duckdb

SOURCE = r"D:\Cyber_DataSet\2026-09-14T10-00-00.json"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "silver")
OUT_PATH = os.path.join(OUT_DIR, "hosts.parquet")

# Only the fields entity resolution + scoring need. Everything else (html,
# screenshot data, raw banner text, device-specific structs) is dropped here.
JSON_COLUMNS = """{
    'ip_str': 'VARCHAR',
    'port': 'BIGINT',
    'transport': 'VARCHAR',
    'timestamp': 'VARCHAR',
    'hostnames': 'VARCHAR[]',
    'domains': 'VARCHAR[]',
    'location': 'STRUCT(country_code VARCHAR, country_name VARCHAR, city VARCHAR, region_code VARCHAR, latitude DOUBLE, longitude DOUBLE)',
    'org': 'VARCHAR',
    'isp': 'VARCHAR',
    'asn': 'VARCHAR',
    'os': 'VARCHAR',
    'product': 'VARCHAR',
    'devicetype': 'VARCHAR',
    'tags': 'VARCHAR[]',
    'cpe23': 'VARCHAR[]',
    'vulns': 'MAP(VARCHAR, STRUCT(cvss DOUBLE, cvss_version DOUBLE, cvss_v2 DOUBLE, summary VARCHAR, verified BOOLEAN, epss DOUBLE, ranking_epss DOUBLE))',
    'ssl': 'STRUCT(jarm VARCHAR)',
    'http': 'STRUCT(status BIGINT, server VARCHAR, title VARCHAR, host VARCHAR)',
    '_shodan': 'STRUCT(module VARCHAR, region VARCHAR, ptr BOOLEAN)'
}"""

SQL = f"""
COPY (
    SELECT
        ip_str,
        port,
        transport,
        try_cast(timestamp AS TIMESTAMP) AS ts,
        hostnames,
        domains,
        location.country_code   AS country_code,
        location.country_name   AS country_name,
        location.city           AS city,
        location.latitude       AS latitude,
        location.longitude      AS longitude,
        org,
        isp,
        asn,
        os,
        product,
        devicetype,
        tags,
        cpe23,
        vulns,
        ssl.jarm                AS ssl_jarm,
        http.status             AS http_status,
        http.server             AS http_server,
        http.title              AS http_title,
        _shodan.module          AS shodan_module,
        list_contains(tags, 'eol-product') AS is_eol_product,
        list_contains(tags, 'self-signed')  AS is_self_signed,
        list_contains(tags, 'honeypot')     AS is_honeypot,
        list_contains(tags, 'c2')           AS is_c2,
        list_contains(tags, 'iot')          AS is_iot,
        list_contains(tags, 'vpn')          AS is_vpn,
        list_contains(tags, 'database')     AS is_database_tag
    FROM read_json(
        '{SOURCE}',
        format = 'newline_delimited',
        columns = {JSON_COLUMNS},
        ignore_errors = true,
        maximum_object_size = 134217728
    )
) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD);
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='4GB'")
    print("Starting Bronze -> Silver Hosts ingestion (this scans all 84.6GB)...")
    t0 = time.time()
    con.execute(SQL)
    elapsed = time.time() - t0
    print(f"Done in {elapsed/60:.1f} min. Wrote {OUT_PATH}")

    count = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    print(f"silver_hosts row count: {count:,}")


if __name__ == "__main__":
    main()
