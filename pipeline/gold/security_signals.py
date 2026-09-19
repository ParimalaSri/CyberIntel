"""Silver Hosts + host_company_map -> Security Signals (urgency inputs).

Per-company aggregation of exposure/urgency signals. See
docs/ARCHITECTURE.md for the fit/urgency scoring split this feeds into.

Limitation: risky_open_port_count means "this port is exposed to the
internet," not "confirmed unauthenticated" - the protocol-specific auth
structs (e.g. redis.authentication_required) were dropped in Silver Hosts
to keep that schema lean. Exposing these ports at all is already a
legitimate risk signal regardless of confirmed auth status.
"""
import os

import duckdb

HOSTS_PATH = r"D:\Cyber_DataSet\data\silver\hosts.parquet"
MAP_PATH = r"D:\Cyber_DataSet\data\silver\host_company_map.parquet"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold")
OUT_PATH = os.path.join(OUT_DIR, "security_signals.parquet")

# port -> label, for documentation/debugging (label unused in SQL, kept for clarity)
RISKY_PORTS = {
    23: "telnet", 21: "ftp", 1433: "mssql", 3389: "rdp",
    5900: "vnc", 5901: "vnc", 5902: "vnc", 5903: "vnc", 5904: "vnc",
    5905: "vnc", 5906: "vnc", 5907: "vnc", 5908: "vnc", 5909: "vnc", 5910: "vnc",
    27017: "mongodb", 6379: "redis", 9200: "elasticsearch", 9300: "elasticsearch",
}
RISKY_PORTS_SQL_LIST = ", ".join(str(p) for p in sorted(RISKY_PORTS))

SQL = f"""
WITH joined AS (
    SELECT h.*, m.company_key, m.resolution_tier
    FROM read_parquet('{HOSTS_PATH}') h
    JOIN read_parquet('{MAP_PATH}') m
        ON h.ip_str IS NOT DISTINCT FROM m.ip_str
        AND h.port = m.port
        AND h.ts = m.ts
),
per_host AS (
    SELECT
        company_key,
        resolution_tier,
        port,
        is_eol_product, is_self_signed, is_honeypot, is_c2,
        is_iot, is_vpn, is_database_tag,
        screenshot_text,
        coalesce(cardinality(vulns), 0) AS row_cve_count,
        CASE WHEN vulns IS NOT NULL AND cardinality(vulns) > 0
             THEN list_max(list_transform(map_values(vulns), x -> x.cvss))
             ELSE NULL END AS row_max_cvss,
        CASE WHEN vulns IS NOT NULL AND cardinality(vulns) > 0
             THEN list_max(list_transform(map_values(vulns), x -> x.epss))
             ELSE NULL END AS row_max_epss,
        CASE WHEN vulns IS NOT NULL AND cardinality(vulns) > 0
             THEN len(list_filter(map_keys(vulns), k -> regexp_matches(k, 'CVE-202[5-9]-'))) > 0
             ELSE false END AS row_has_recent_cve,
        CASE WHEN vulns IS NOT NULL AND cardinality(vulns) > 0
             THEN len(list_filter(map_values(vulns), x -> x.verified))
             ELSE 0 END AS row_verified_cve_count,
        (port IN ({RISKY_PORTS_SQL_LIST})) AS is_risky_port
    FROM joined
)
SELECT
    company_key,
    resolution_tier,
    count(*) AS host_count,
    CAST(coalesce(sum(row_cve_count), 0) AS BIGINT) AS cve_count,
    CAST(coalesce(sum(row_verified_cve_count), 0) AS BIGINT) AS verified_cve_count,
    max(row_max_cvss) AS max_cvss,
    max(row_max_epss) AS max_epss,
    bool_or(row_has_recent_cve) AS has_recent_cve,
    CAST(coalesce(sum(is_eol_product::INT), 0) AS BIGINT) AS eol_count,
    CAST(coalesce(sum(is_self_signed::INT), 0) AS BIGINT) AS self_signed_count,
    CAST(coalesce(sum(is_honeypot::INT), 0) AS BIGINT) AS honeypot_count,
    CAST(coalesce(sum(is_c2::INT), 0) AS BIGINT) AS c2_count,
    CAST(coalesce(sum(is_iot::INT), 0) AS BIGINT) AS iot_count,
    CAST(coalesce(sum(is_vpn::INT), 0) AS BIGINT) AS vpn_count,
    CAST(coalesce(sum(is_database_tag::INT), 0) AS BIGINT) AS database_tag_count,
    CAST(coalesce(sum(is_risky_port::INT), 0) AS BIGINT) AS risky_open_port_count,
    count(DISTINCT port) AS distinct_port_count,
    bool_or(screenshot_text IS NOT NULL) AS has_screenshot_evidence,
    max(screenshot_text) AS screenshot_evidence_text
FROM per_host
GROUP BY company_key, resolution_tier
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='4GB'")

    con.execute(f"COPY ({SQL}) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    n = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    print(f"security_signals row count: {n:,}")

    print("\n--- Sanity: companies with the highest urgency signals ---")
    for row in con.execute(f"""
        SELECT company_key, resolution_tier, cve_count, max_cvss, max_epss, has_recent_cve,
               eol_count, self_signed_count, risky_open_port_count
        FROM read_parquet('{OUT_PATH}')
        WHERE cve_count > 0
        ORDER BY max_epss DESC NULLS LAST
        LIMIT 10
    """).fetchall():
        print(row)

    print("\n--- Distribution: any urgency signal at all ---")
    for row in con.execute(f"""
        SELECT
            sum((cve_count > 0)::INT) AS with_cve,
            sum((eol_count > 0)::INT) AS with_eol,
            sum((self_signed_count > 0)::INT) AS with_self_signed,
            sum((honeypot_count > 0)::INT) AS with_honeypot,
            sum((c2_count > 0)::INT) AS with_c2,
            sum((risky_open_port_count > 0)::INT) AS with_risky_port,
            count(*) AS total
        FROM read_parquet('{OUT_PATH}')
    """).fetchall():
        print(row)


if __name__ == "__main__":
    main()
