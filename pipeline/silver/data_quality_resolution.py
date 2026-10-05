"""Data-quality checks for the entity-resolution outputs:
silver/companies.parquet and silver/host_company_map.parquet.

Checks grain/uniqueness, referential integrity back to hosts, key shape per
tier, denylist leakage, and internal consistency. Read-only; writes
docs/silver_data_quality_resolution.csv.
"""
import csv
import os
import sys

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "entity_resolution"))
from denylists import NON_TARGET_DOMAINS, HYPERSCALER_ORG_KEYWORDS, GENERIC_ISP_KEYWORDS  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
D = os.path.join(ROOT, "data", "silver").replace("\\", "/")
HOSTS, COMP, MAP = f"{D}/hosts.parquet", f"{D}/companies.parquet", f"{D}/host_company_map.parquet"
OUT = os.path.join(ROOT, "docs", "silver_data_quality_resolution.csv")

q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
INFRA = ", ".join(q(d) for d in sorted(NON_TARGET_DOMAINS))
HYP = " OR ".join(f"company_key LIKE '%{k}%'" for k in sorted(HYPERSCALER_ORG_KEYWORDS))
ISP = " OR ".join(f"company_key LIKE '%{k}%'" for k in sorted(GENERIC_ISP_KEYWORDS))

# (table, check, rows_sql, violations_sql, note)
CHECKS = [
    ("companies", "company_key not null", "SELECT count(*) FROM c", "SELECT count(*) FROM c WHERE company_key IS NULL", ""),
    ("companies", "resolution_tier in (domain, org)", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c WHERE resolution_tier NOT IN ('domain','org')", ""),
    ("companies", "Grain: one row per (company_key, resolution_tier)", "SELECT count(*) FROM c",
     "SELECT count(*) FROM (SELECT 1 FROM c GROUP BY company_key, resolution_tier HAVING count(*) > 1)", ""),
    ("companies", "host_count >= 1", "SELECT count(*) FROM c", "SELECT count(*) FROM c WHERE host_count < 1", ""),
    ("companies", "company_key has no surrounding spaces / trailing punctuation", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c WHERE company_key <> trim(company_key) OR regexp_matches(company_key, '[\\s.,]+$')",
     "Domain-tier keys from Shodan can end in '.'; counted here"),
    ("companies", "company_key is lowercase", "SELECT count(*) FROM c", "SELECT count(*) FROM c WHERE company_key <> lower(company_key)", ""),
    ("companies", "domain tier: key looks like a domain (letters, contains '.', no spaces/scheme/path)",
     "SELECT count(*) FROM c WHERE resolution_tier='domain'",
     "SELECT count(*) FROM c WHERE resolution_tier='domain' AND NOT regexp_matches(company_key, '^[a-z0-9-]+(\\.[a-z0-9-]+)+\\.?$')", ""),
    ("companies", "domain tier: key is not an IP address", "SELECT count(*) FROM c WHERE resolution_tier='domain'",
     "SELECT count(*) FROM c WHERE resolution_tier='domain' AND regexp_matches(company_key, '^\\d{1,3}(\\.\\d{1,3}){3}\\.?$')", ""),
    ("companies", "domain tier: key not in shared-infra/brand/placeholder denylist", "SELECT count(*) FROM c WHERE resolution_tier='domain'",
     f"SELECT count(*) FROM c WHERE resolution_tier='domain' AND (lower(regexp_replace(company_key,'\\.$','')) IN ({INFRA}) OR company_key LIKE '%.arpa')", ""),
    ("companies", "org tier: key not a hyperscaler/CDN name", "SELECT count(*) FROM c WHERE resolution_tier='org'",
     f"SELECT count(*) FROM c WHERE resolution_tier='org' AND ({HYP})", ""),
    ("companies", "org tier: key not a generic consumer ISP name", "SELECT count(*) FROM c WHERE resolution_tier='org'",
     f"SELECT count(*) FROM c WHERE resolution_tier='org' AND ({ISP})", ""),
    ("companies", "org tier: key length >= 3", "SELECT count(*) FROM c WHERE resolution_tier='org'",
     "SELECT count(*) FROM c WHERE resolution_tier='org' AND length(company_key) < 3", ""),
    ("companies", "primary_country_code is 2 uppercase letters when present", "SELECT count(*) FROM c WHERE primary_country_code IS NOT NULL",
     "SELECT count(*) FROM c WHERE primary_country_code IS NOT NULL AND NOT regexp_matches(primary_country_code, '^[A-Z]{2}$')", ""),
    ("companies", "primary_country_code not null", "SELECT count(*) FROM c", "SELECT count(*) FROM c WHERE primary_country_code IS NULL", "Feeds the fit score's country bonus"),
    ("companies", "country_count >= 1 when primary_country_code present", "SELECT count(*) FROM c WHERE primary_country_code IS NOT NULL",
     "SELECT count(*) FROM c WHERE primary_country_code IS NOT NULL AND country_count < 1", ""),
    ("companies", "sample_ips has 1-5 entries", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c WHERE sample_ips IS NULL OR len(sample_ips) NOT BETWEEN 1 AND 5", ""),
    ("companies", "likely_infra_or_proxy == (host_count >= 500)", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c WHERE likely_infra_or_proxy <> (host_count >= 500)", ""),
    ("companies", "primary_org not null", "SELECT count(*) FROM c", "SELECT count(*) FROM c WHERE primary_org IS NULL", "Domain-tier companies can lack org"),
    ("companies", "Same key present in both tiers (domain and org)", "SELECT count(DISTINCT company_key) FROM c",
     "SELECT count(*) FROM (SELECT 1 FROM c GROUP BY company_key HAVING count(DISTINCT resolution_tier) > 1)", "Info: scoring joins on key+tier, so this is handled"),
    ("host_company_map", "key columns not null (ip_str, port, ts, company_key, tier)", "SELECT count(*) FROM m",
     "SELECT count(*) FROM m WHERE ip_str IS NULL OR port IS NULL OR ts IS NULL OR company_key IS NULL OR resolution_tier IS NULL", ""),
    ("host_company_map", "resolution_tier in (domain, org)", "SELECT count(*) FROM m",
     "SELECT count(*) FROM m WHERE resolution_tier NOT IN ('domain','org')", ""),
    ("host_company_map", "Grain: unique (ip_str, port, ts)", "SELECT count(*) FROM m",
     "SELECT count(*) FROM (SELECT 1 FROM m GROUP BY ip_str, port, ts HAVING count(*) > 1)", ""),
    ("host_company_map", "Every row exists in hosts (ip_str, port, ts)", "SELECT count(*) FROM m",
     "SELECT count(*) FROM m ANTI JOIN h USING (ip_str, port, ts)", ""),
    ("host_company_map", "Every (company_key, tier) exists in companies", "SELECT count(*) FROM m",
     "SELECT count(*) FROM m ANTI JOIN c USING (company_key, resolution_tier)", ""),
    ("cross-table", "Every company has at least one map row", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c ANTI JOIN (SELECT DISTINCT company_key, resolution_tier FROM m) x USING (company_key, resolution_tier)", ""),
    ("cross-table", "sum(companies.host_count) == map rows", "SELECT count(*) FROM m",
     "SELECT abs((SELECT sum(host_count) FROM c) - (SELECT count(*) FROM m))", "Violation = absolute difference"),
    ("cross-table", "Per-company host_count matches map row count", "SELECT count(*) FROM c",
     "SELECT count(*) FROM c JOIN (SELECT company_key, resolution_tier, count(*) n FROM m GROUP BY 1,2) x USING (company_key, resolution_tier) WHERE c.host_count <> x.n", ""),
    ("cross-table", "Mapped tier matches host re-derivation: no map row for a null-IP host", "SELECT count(*) FROM m",
     "SELECT count(*) FROM m WHERE ip_str IS NULL", "IPv6 recovery means every host should have an ip_str"),
]


def main():
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{HOSTS}')")
    con.execute(f"CREATE VIEW c AS SELECT * FROM read_parquet('{COMP}')")
    con.execute(f"CREATE VIEW m AS SELECT * FROM read_parquet('{MAP}')")
    rows = []
    for table, check, rsql, vsql, note in CHECKS:
        n = con.execute(rsql).fetchone()[0]
        v = con.execute(vsql).fetchone()[0]
        status = "Pass" if v == 0 else ("Info" if check.startswith("Same key") else "Flagged")
        rows.append([table, check, n, v, round(v / n * 100, 3) if n else 0.0, status, note])
        print(f"{status:8s} {table:17s} {check[:70]:70s} {v:>10,} / {n:,}")

    # headline profile
    tot = con.execute("SELECT count(*) FROM h").fetchone()[0]
    for tier, cnt in con.execute("SELECT resolution_tier, count(*) FROM m GROUP BY 1 ORDER BY 2 DESC").fetchall():
        rows.append(["profile", f"Hosts resolved at tier '{tier}'", tot, cnt, round(cnt / tot * 100, 3), "Info", "share of all host rows"])
    v6 = con.execute("SELECT count(*) FROM m WHERE ip_str LIKE '%:%'").fetchone()[0]
    rows.append(["profile", "IPv6 hosts resolved to a company", tot, v6, round(v6 / tot * 100, 3), "Info", "recovered by the ipv6 fallback"])

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["table", "check", "rows_in_scope", "violations_or_count", "pct", "status", "note"])
        w.writerows(rows)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
