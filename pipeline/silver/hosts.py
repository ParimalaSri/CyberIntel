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
# raw banner text, cert extensions/chain, rare service structs) is dropped here.
JSON_COLUMNS = """{
    'ip_str': 'VARCHAR',
    'ipv6': 'VARCHAR',
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
    'vulns': 'MAP(VARCHAR, STRUCT(cvss DOUBLE, cvss_version DOUBLE, cvss_v2 DOUBLE, summary VARCHAR, verified BOOLEAN, epss DOUBLE, ranking_epss DOUBLE, kev BOOLEAN, ransomware_campaign VARCHAR))',
    'version': 'VARCHAR',
    'cloud': 'STRUCT(provider VARCHAR, region VARCHAR, service VARCHAR)',
    'ssl': 'STRUCT(
        jarm VARCHAR,
        versions VARCHAR[],
        cert STRUCT(
            expires VARCHAR, expired BOOLEAN, sig_alg VARCHAR,
            subject STRUCT(CN VARCHAR, O VARCHAR, C VARCHAR),
            issuer STRUCT(CN VARCHAR, O VARCHAR)))',
    'http': 'STRUCT(
        status BIGINT, server VARCHAR, title VARCHAR, host VARCHAR,
        waf VARCHAR,
        components MAP(VARCHAR, STRUCT(categories VARCHAR[])))',
    '_shodan': 'STRUCT(module VARCHAR, region VARCHAR, ptr BOOLEAN)',
    'screenshot': 'STRUCT(labels VARCHAR[], text VARCHAR)'
}"""

# Cleaning rules (see docs/silver_data_quality_report.csv for the evidence).
CVE_RE = r"^(CVE-\d{4}-\d{4,}|MS\d{2}-\d{3})$"  # MS bulletin ids (MS17-010) are valid
STR_COLS = [
    "org", "isp", "os", "product", "product_version", "devicetype", "city",
    "country_name", "region_code", "http_server", "http_title", "http_waf",
    "ssl_subject_cn", "ssl_subject_org", "ssl_issuer_org", "ssl_issuer_cn",
    "ssl_sig_alg", "cloud_provider", "cloud_region", "cloud_service",
    "shodan_module", "screenshot_text", "ssl_jarm",
]
STR_CLEAN = ", ".join(f"nullif(trim({c}), '') AS {c}" for c in STR_COLS)
# strip scheme, lowercase/trim, drop blanks and anything still holding a path
HOST_CLEAN = ("list_filter(list_transform({col}, x -> lower(trim(regexp_replace(x, '^[a-zA-Z]+://', '')))), "
              "x -> x <> '' AND x NOT LIKE '%/%'{extra})")
# list_contains(NULL, ..) is NULL when tags is NULL; a missing tag means false
NOT_BARE_IP = r" AND NOT regexp_matches(x, '^\d{1,3}(\.\d{1,3}){3}$')"
FLAG_CLEAN = ", ".join(f"coalesce({f}, false) AS {f}" for f in [
    "is_eol_product", "is_self_signed", "is_honeypot", "is_c2", "is_iot", "is_vpn", "is_database_tag"])

SQL = f"""
COPY (
SELECT * REPLACE (
    -- empty list/map -> NULL so "no value" has one representation
    CASE WHEN len(hostnames) = 0 THEN NULL ELSE hostnames END AS hostnames,
    CASE WHEN len(domains) = 0 THEN NULL ELSE domains END AS domains,
    CASE WHEN len(tags) = 0 THEN NULL ELSE tags END AS tags,
    CASE WHEN len(cpe23) = 0 THEN NULL ELSE cpe23 END AS cpe23,
    CASE WHEN len(screenshot_labels) = 0 THEN NULL ELSE screenshot_labels END AS screenshot_labels,
    CASE WHEN cardinality(vulns) = 0 THEN NULL ELSE vulns END AS vulns
) FROM (
SELECT * REPLACE (
    {STR_CLEAN},
    lower(trim(transport)) AS transport,
    nullif(upper(trim(country_code)), '') AS country_code,
    nullif(upper(trim(asn)), '') AS asn,
    -- a status outside 100-599 is a corrupt banner parse, not a real response
    CASE WHEN http_status BETWEEN 100 AND 599 THEN http_status END AS http_status,
    -- a bare IP literal as a "hostname" says nothing about the owner
    {HOST_CLEAN.format(col="hostnames", extra=NOT_BARE_IP)} AS hostnames,
    -- junk like '232.' or '.' has no letters, so it cannot be a domain
    {HOST_CLEAN.format(col="domains", extra=" AND regexp_matches(x, '[a-zA-Z]')")} AS domains,
    map_from_entries(list_filter(map_entries(vulns), e -> regexp_matches(e.key, '{CVE_RE}'))) AS vulns,
    {FLAG_CLEAN}
) FROM (
    SELECT
        -- IPv6-only hosts have no ip_str in the source; their address is in ipv6
        coalesce(ip_str, ipv6) AS ip_str,
        port,
        transport,
        try_cast(timestamp AS TIMESTAMP) AS ts,
        hostnames,
        domains,
        location.country_code   AS country_code,
        location.country_name   AS country_name,
        location.city           AS city,
        nullif(location.region_code, '') AS region_code,
        location.latitude       AS latitude,
        location.longitude      AS longitude,
        org,
        isp,
        asn,
        os,
        product,
        nullif(version, '')     AS product_version,
        devicetype,
        tags,
        cpe23,
        vulns,
        cloud.provider          AS cloud_provider,
        cloud.region            AS cloud_region,
        cloud.service           AS cloud_service,
        ssl.jarm                AS ssl_jarm,
        ssl.cert.subject.CN     AS ssl_subject_cn,
        ssl.cert.subject.O      AS ssl_subject_org,
        ssl.cert.issuer.O       AS ssl_issuer_org,
        ssl.cert.issuer.CN      AS ssl_issuer_cn,
        try_strptime(ssl.cert.expires, '%Y%m%d%H%M%SZ') AS ssl_expires,
        ssl.cert.expired        AS ssl_expired,
        ssl.cert.sig_alg        AS ssl_sig_alg,
        -- ssl.versions lists "TLSv1.2" (enabled) or "-TLSv1" (disabled)
        list_has_any(ssl.versions, ['SSLv2', 'SSLv3', 'TLSv1', 'TLSv1.1']) AS ssl_legacy_tls_enabled,
        http.status             AS http_status,
        http.server             AS http_server,
        http.title              AS http_title,
        http.waf                AS http_waf,
        map_keys(http.components) AS http_components,
        _shodan.module          AS shodan_module,
        screenshot.labels       AS screenshot_labels,
        nullif(screenshot.text, '') AS screenshot_text,
        list_contains(tags, 'eol-product') AS is_eol_product,
        list_contains(tags, 'self-signed')  AS is_self_signed,
        list_contains(tags, 'honeypot')     AS is_honeypot,
        list_contains(tags, 'c2')           AS is_c2,
        list_contains(tags, 'iot')          AS is_iot,
        list_contains(tags, 'vpn')          AS is_vpn,
        list_contains(tags, 'database')     AS is_database_tag,
        coalesce(list_bool_or(list_transform(map_values(vulns), x -> coalesce(x.kev, false))), false) AS has_kev_cve,
        coalesce(list_bool_or(list_transform(map_values(vulns), x -> x.ransomware_campaign = 'Known')), false) AS has_ransomware_cve
    FROM read_json(
        '{SOURCE}',
        format = 'newline_delimited',
        columns = {JSON_COLUMNS},
        ignore_errors = true,
        maximum_object_size = 134217728
    )
)
-- a row with neither ip_str nor ipv6 has no host identity to key on
WHERE ip_str IS NOT NULL
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
