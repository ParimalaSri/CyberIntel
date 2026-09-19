"""Silver Companies + Security Signals -> Gold Accounts.

Combines fit (is this the right kind of customer) and urgency (are they
exposed right now) into a single contact_score and band, with a why[]
explanation array. See docs/ARCHITECTURE.md for the rubric and the
rule-vs-LLM split this feeds into (bands 50-84 go to LLM adjudication in
Sprint 4 - this script only computes the deterministic rule-based score).
"""
import os

import duckdb

COMPANIES_PATH = r"D:\Cyber_DataSet\data\silver\companies.parquet"
SIGNALS_PATH = r"D:\Cyber_DataSet\data\gold\security_signals.parquet"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold")
OUT_PATH = os.path.join(OUT_DIR, "accounts.parquet")

# Fit: geography bonus. Not exhaustive - a starting target-market list,
# tunable per go-to-market strategy.
TARGET_COUNTRIES = {"US", "GB", "CA", "DE", "FR", "AU", "NL", "SE", "CH", "SG", "JP"}
TARGET_COUNTRIES_SQL = ", ".join(f"'{c}'" for c in sorted(TARGET_COUNTRIES))

SQL = f"""
WITH base AS (
    SELECT
        c.company_key,
        c.resolution_tier,
        c.host_count,
        c.primary_country_code,
        c.primary_country_name,
        c.asn_count,
        c.primary_org,
        c.primary_isp,
        c.sample_ips,
        c.sample_hostnames,
        c.likely_infra_or_proxy,
        s.cve_count, s.verified_cve_count, s.max_cvss, s.max_epss, s.has_recent_cve,
        s.eol_count, s.self_signed_count, s.honeypot_count, s.c2_count,
        s.risky_open_port_count, s.distinct_port_count, s.database_tag_count,
        s.has_screenshot_evidence, s.screenshot_evidence_text
    FROM read_parquet('{COMPANIES_PATH}') c
    JOIN read_parquet('{SIGNALS_PATH}') s
        ON c.company_key = s.company_key AND c.resolution_tier = s.resolution_tier
),
scored AS (
    SELECT *,
        -- Fit (0-100): is this the right kind of customer
        (CASE WHEN resolution_tier = 'domain' THEN 40 ELSE 0 END)
        + (CASE WHEN likely_infra_or_proxy THEN 0
                ELSE LEAST(host_count, 20) / 20.0 * 20 END)
        + (CASE WHEN database_tag_count > 0 THEN 20 ELSE 5 END)
        + (CASE WHEN primary_country_code IN ({TARGET_COUNTRIES_SQL}) THEN 20 ELSE 10 END)
        AS fit_score,
        -- Urgency (0-100): how exposed are they right now
        (CASE WHEN c2_count > 0 OR has_screenshot_evidence THEN 100 ELSE
            LEAST(25 * coalesce(max_epss, 0), 25)
            + (CASE WHEN has_recent_cve THEN 10 ELSE 0 END)
            + (CASE WHEN verified_cve_count > 0 THEN 10 ELSE 0 END)
            + (CASE WHEN eol_count > 0 THEN 15 ELSE 0 END)
            + (CASE WHEN self_signed_count > 0 THEN 10 ELSE 0 END)
            + (CASE WHEN risky_open_port_count > 0 THEN 20 ELSE 0 END)
        END) AS urgency_score
    FROM base
),
final AS (
    SELECT *,
        round(0.5 * fit_score + 0.5 * urgency_score) AS contact_score
    FROM scored
)
SELECT *,
    CASE
        WHEN honeypot_count > 0 OR likely_infra_or_proxy THEN 'exclude'
        WHEN contact_score >= 85 THEN 'contact'
        WHEN contact_score >= 50 THEN 'review'
        ELSE 'skip'
    END AS band,
    list_filter([
        CASE WHEN has_screenshot_evidence THEN
            'Screenshot evidence of an exposed remote desktop login' ||
            (CASE WHEN screenshot_evidence_text IS NOT NULL
                  THEN ' (OCR text visible on screen: "' || left(screenshot_evidence_text, 120) || '")'
                  ELSE '' END)
        ELSE NULL END,
        CASE WHEN cve_count > 0 THEN
            cve_count || ' known CVE(s), max CVSS ' || max_cvss ||
            ', max EPSS ' || round(coalesce(max_epss, 0), 2)
        ELSE NULL END,
        CASE WHEN verified_cve_count > 0 THEN
            verified_cve_count || ' of those CVE(s) are human-verified matches, not just CPE-inferred'
        ELSE NULL END,
        CASE WHEN has_recent_cve THEN 'Includes a 2025+ (recently disclosed) CVE' ELSE NULL END,
        CASE WHEN eol_count > 0 THEN eol_count || ' host(s) running end-of-life software' ELSE NULL END,
        CASE WHEN self_signed_count > 0 THEN self_signed_count || ' host(s) with self-signed TLS certificates' ELSE NULL END,
        CASE WHEN risky_open_port_count > 0 THEN
            risky_open_port_count || ' host(s) with a high-risk port exposed (VNC/RDP/MSSQL/Telnet/FTP/MongoDB/Redis/Elasticsearch)'
        ELSE NULL END,
        CASE WHEN c2_count > 0 THEN 'Possible active compromise: ' || c2_count || ' host(s) flagged for C2 activity' ELSE NULL END,
        CASE WHEN database_tag_count > 0 THEN database_tag_count || ' exposed database service(s)' ELSE NULL END,
        CASE WHEN honeypot_count > 0 THEN 'Excluded: honeypot tag present (not a real production asset)' ELSE NULL END,
        CASE WHEN likely_infra_or_proxy THEN 'Excluded: looks like infrastructure/proxy network, not a single business' ELSE NULL END
    ], x -> x IS NOT NULL) AS why
FROM final
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA memory_limit='4GB'")

    con.execute(f"COPY ({SQL}) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    n = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    print(f"gold_accounts row count: {n:,}\n")

    print("--- Band distribution ---")
    for row in con.execute(f"""
        SELECT band, count(*) AS n, round(avg(contact_score),1) AS avg_score
        FROM read_parquet('{OUT_PATH}') GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        print(row)

    print("\n--- Top 10 'contact' band accounts ---")
    for row in con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, fit_score, urgency_score, why
        FROM read_parquet('{OUT_PATH}')
        WHERE band = 'contact'
        ORDER BY contact_score DESC LIMIT 10
    """).fetchall():
        print(row)

    print("\n--- Sample 5 'review' band accounts (LLM adjudication candidates) ---")
    for row in con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, fit_score, urgency_score, why
        FROM read_parquet('{OUT_PATH}')
        WHERE band = 'review'
        USING SAMPLE 5
    """).fetchall():
        print(row)


if __name__ == "__main__":
    main()
