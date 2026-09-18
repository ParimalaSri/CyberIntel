"""Silver Hosts -> Entity Resolution -> Silver Companies.

Resolves each host row to a company using three confidence tiers (see
docs/ARCHITECTURE.md "Entity resolution is heuristic, not ground truth"):

  domain  - a `domains` entry survives the hyperscaler/CDN denylist and the
            hostname isn't an ISP-assigned dynamic PTR pattern. High confidence.
  org     - no usable domain, but `org` isn't a hyperscaler/generic-ISP name.
            Medium confidence, not independently contactable (no domain).
  unresolved - neither. Kept as a counted bucket, not silently dropped.
"""
import os
import sys

import duckdb

sys.path.insert(0, os.path.dirname(__file__))
from denylists import (  # noqa: E402
    SHARED_INFRA_DOMAIN_SUFFIXES,
    HYPERSCALER_ORG_KEYWORDS,
    GENERIC_ISP_KEYWORDS,
)

HOSTS_PATH = r"D:\Cyber_DataSet\data\silver\hosts.parquet"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "silver")
OUT_PATH = os.path.join(OUT_DIR, "companies.parquet")

_SQL_STR = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731

SHARED_INFRA_SQL_LIST = ", ".join(_SQL_STR(d) for d in sorted(SHARED_INFRA_DOMAIN_SUFFIXES))

HYPERSCALER_LIKE_SQL = " OR ".join(
    f"coalesce(lower(org), '') LIKE '%{k}%' OR coalesce(lower(isp), '') LIKE '%{k}%'"
    for k in sorted(HYPERSCALER_ORG_KEYWORDS)
)

GENERIC_ISP_LIKE_SQL = " OR ".join(
    f"coalesce(lower(org), '') LIKE '%{k}%'" for k in sorted(GENERIC_ISP_KEYWORDS)
)

# ISP-assigned dynamic/PTR hostname pattern, e.g. "77-163-107-27.fixed.kpn.net"
# or "ip-192-168-1-1.customer...": the leading label is the IP itself, dashed.
DYNAMIC_PTR_REGEX = r'^(ip-)?[0-9]+(-[0-9]+){2,}'

SQL = f"""
WITH base AS (
    SELECT
        ip_str, port, org, isp, asn, country_code, country_name, city,
        hostnames, domains,
        CASE WHEN len(domains) > 0 THEN
            list_filter(domains, d -> lower(d) NOT IN ({SHARED_INFRA_SQL_LIST}))[1]
        ELSE NULL END AS candidate_domain,
        CASE WHEN len(hostnames) > 0
            THEN regexp_matches(hostnames[1], '{DYNAMIC_PTR_REGEX}')
            ELSE false
        END AS is_dynamic_ptr,
        ({HYPERSCALER_LIKE_SQL}) AS is_hyperscaler_org,
        ({GENERIC_ISP_LIKE_SQL}) AS is_generic_isp_org
    FROM read_parquet('{HOSTS_PATH}')
),
tiered AS (
    SELECT *,
        CASE
            WHEN candidate_domain IS NOT NULL AND NOT is_dynamic_ptr AND NOT is_hyperscaler_org
                THEN 'domain'
            WHEN org IS NOT NULL AND NOT is_hyperscaler_org AND NOT is_generic_isp_org
                THEN 'org'
            ELSE 'unresolved'
        END AS resolution_tier
    FROM base
),
keyed AS (
    SELECT *,
        CASE resolution_tier
            WHEN 'domain' THEN lower(candidate_domain)
            WHEN 'org' THEN lower(trim(org))
            ELSE NULL
        END AS company_key
    FROM tiered
)
SELECT * FROM keyed
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='4GB'")
    con.execute(f"CREATE VIEW keyed AS {SQL}")

    total = con.execute("SELECT count(*) FROM keyed").fetchone()[0]
    print(f"Total host rows: {total:,}\n")

    print("--- Resolution yield ---")
    for row in con.execute(
        "SELECT resolution_tier, count(*) AS n FROM keyed GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall():
        tier, n = row
        print(f"{tier:12s} {n:>10,} ({n/total*100:5.1f}%)")

    print("\n--- Sample unresolved orgs (QA) ---")
    for row in con.execute(
        """SELECT org, isp, count(*) AS n FROM keyed
           WHERE resolution_tier = 'unresolved' AND org IS NOT NULL
           GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15"""
    ).fetchall():
        print(row)

    print(f"\nWriting {OUT_PATH} ...")
    con.execute(f"""
        COPY (
            SELECT
                company_key,
                resolution_tier,
                count(*) AS host_count,
                count(DISTINCT country_code) AS country_count,
                mode(country_code) AS primary_country_code,
                mode(country_name) AS primary_country_name,
                count(DISTINCT asn) AS asn_count,
                mode(org) AS primary_org,
                mode(isp) AS primary_isp,
                list(DISTINCT ip_str)[1:5] AS sample_ips,
                list(DISTINCT hostnames[1])[1:5] AS sample_hostnames
            FROM keyed
            WHERE resolution_tier IN ('domain', 'org')
            GROUP BY company_key, resolution_tier
        ) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n_companies = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    print(f"silver_companies row count: {n_companies:,}")


if __name__ == "__main__":
    main()
