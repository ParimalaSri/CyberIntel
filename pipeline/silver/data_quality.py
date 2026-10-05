"""Silver Hosts data-quality report: BEFORE vs AFTER remediation (dry run).

BEFORE = data/silver/hosts.parquet exactly as written by hosts.py.
AFTER  = the same data with the cleaning rules in CLEAN_SQL applied in a
temporary DuckDB view. Silver itself is never modified.

Checks come from testing/shodan_data_dictionary_sample_based (1).xlsx
(source = 'Excel'); extra consistency/range checks we added are source =
'Added'. Excel checks on fields that are not loaded into Silver are listed
as 'Not run' with the reason.

Writes docs/silver_data_quality_report.csv and docs/silver_null_profile.csv.
"""
import csv
import os
import sys

import duckdb

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PATH = os.path.join(ROOT, "data", "silver", "hosts.parquet").replace("\\", "/")
# optional suffix keeps a re-run on the rebuilt Silver from overwriting the earlier report:
#   python data_quality.py _post_rebuild
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""
REPORT = os.path.join(ROOT, "docs", f"silver_data_quality_report{SUFFIX}.csv")
NULLS = os.path.join(ROOT, "docs", f"silver_null_profile{SUFFIX}.csv")

CVE_RE = r"^(CVE-\d{4}-\d{4,}|MS\d{2}-\d{3})$"  # MS bulletins (MS17-010) are valid ids
STR_COLS = [
    "org", "isp", "os", "product", "product_version", "devicetype", "city",
    "country_name", "region_code", "http_server", "http_title", "http_waf",
    "ssl_subject_cn", "ssl_subject_org", "ssl_issuer_org", "ssl_issuer_cn",
    "ssl_sig_alg", "cloud_provider", "cloud_region", "cloud_service",
    "shodan_module", "screenshot_text", "ssl_jarm",
]
FLAGS = ["is_eol_product", "is_self_signed", "is_honeypot", "is_c2", "is_iot",
         "is_vpn", "is_database_tag"]


def host_clean(col):
    # strip scheme, lowercase/trim, drop blanks and anything still holding a path
    return (f"list_filter(list_transform({col}, x -> lower(trim(regexp_replace(x, '^[a-zA-Z]+://', '')))), "
            f"x -> x <> '' AND x NOT LIKE '%/%')")


CLEAN_SQL = f"""
CREATE OR REPLACE VIEW c1 AS
SELECT * REPLACE (
    {", ".join(f"nullif(trim({c}), '') AS {c}" for c in STR_COLS)},
    lower(trim(transport)) AS transport,
    upper(trim(country_code)) AS country_code,
    upper(trim(asn)) AS asn,
    CASE WHEN http_status BETWEEN 100 AND 599 THEN http_status END AS http_status,
    {host_clean("hostnames")} AS hostnames,
    {host_clean("domains")} AS domains,
    map_from_entries(list_filter(map_entries(vulns), e -> regexp_matches(e.key, '{CVE_RE}'))) AS vulns,
    {", ".join(f"coalesce({f}, false) AS {f}" for f in FLAGS)}
)
FROM read_parquet('{PATH}')
WHERE ip_str IS NOT NULL;

CREATE OR REPLACE VIEW c AS
SELECT * REPLACE (
    CASE WHEN len(hostnames) = 0 THEN NULL ELSE hostnames END AS hostnames,
    CASE WHEN len(domains) = 0 THEN NULL ELSE domains END AS domains,
    CASE WHEN len(tags) = 0 THEN NULL ELSE tags END AS tags,
    CASE WHEN len(cpe23) = 0 THEN NULL ELSE cpe23 END AS cpe23,
    CASE WHEN len(screenshot_labels) = 0 THEN NULL ELSE screenshot_labels END AS screenshot_labels,
    CASE WHEN cardinality(vulns) = 0 THEN NULL ELSE vulns END AS vulns
)
FROM c1;
"""

IPV4 = r"^(\d{1,3}\.){3}\d{1,3}$"
NO_PATH = "x -> x LIKE '%/%' OR regexp_matches(x, '^[a-zA-Z]+://')"
# (field, check, source, scope_sql, bad_sql, remediation)
CHECKS = [
    ("ip_str", "Not null", "Excel", "true", "ip_str IS NULL",
     "Rows quarantined (excluded). Cause: IPv6 hosts - source 'ipv6' field is not loaded; loading it into ip_str would recover them."),
    ("ip_str", "Valid IPv4 (4 octets, each 0-255) or IPv6 (contains ':')", "Excel", "ip_str IS NOT NULL",
     f"ip_str NOT LIKE '%:%' AND (NOT regexp_matches(ip_str, '{IPV4}') OR list_max(list_transform(string_split(ip_str, '.'), x -> try_cast(x AS INT))) > 255)",
     "None needed"),
    ("ip_str", "No leading/trailing spaces", "Excel", "ip_str IS NOT NULL", "ip_str <> trim(ip_str)", "trim"),
    ("port", "Not null", "Excel", "true", "port IS NULL", "None needed"),
    ("port", "Integer 1-65535", "Excel", "port IS NOT NULL", "port < 1 OR port > 65535", "None needed"),
    ("transport", "Not null", "Excel", "true", "transport IS NULL", "None needed"),
    ("transport", "Allowed values tcp/udp", "Excel", "transport IS NOT NULL", "transport NOT IN ('tcp', 'udp')", "None needed"),
    ("transport", "Lowercase", "Excel", "transport IS NOT NULL", "transport <> lower(transport)", "lower()"),
    ("ts", "Not null / parseable timestamp", "Excel", "true", "ts IS NULL", "None needed"),
    ("ts", "Not in the future", "Excel", "ts IS NOT NULL", "ts > now()", "None needed"),
    ("hostnames", "Empty list stored as [] instead of NULL", "Excel", "true", "len(hostnames) = 0", "Empty list -> NULL"),
    ("hostnames", "Lowercase", "Added", "len(hostnames) > 0", "list_bool_or(list_transform(hostnames, x -> x <> lower(x)))", "lower()"),
    ("hostnames", "No scheme (http://) or path (/)", "Added", "len(hostnames) > 0",
     f"list_bool_or(list_transform(hostnames, {NO_PATH}))",
     "Strip scheme; drop entries that still contain a path"),
    ("domains", "Empty exactly when hostnames is empty", "Excel", "true",
     "(coalesce(len(hostnames), 0) = 0) <> (coalesce(len(domains), 0) = 0)", "None needed"),
    ("domains", "Lowercase", "Excel", "len(domains) > 0", "list_bool_or(list_transform(domains, x -> x <> lower(x)))", "lower()"),
    ("domains", "No scheme or path", "Excel", "len(domains) > 0",
     f"list_bool_or(list_transform(domains, {NO_PATH}))", "Strip scheme; drop entries that still contain a path"),
    ("domains", "Each domain is contained in a hostname", "Excel", "len(domains) > 0",
     "NOT list_bool_and(list_transform(domains, d -> list_bool_or(list_transform(hostnames, x -> x = d OR x LIKE '%.' || d))))",
     "Flag only: Shodan emits partial labels like 'home.' / 'dynamic.'; not a safe auto-fix"),
    ("org", "Not null", "Excel", "true", "org IS NULL", "None possible (value unknown)"),
    ("org", "Trimmed, no empty string", "Excel", "org IS NOT NULL", "org <> trim(org) OR org = ''", "trim + ''->NULL"),
    ("isp", "Trimmed, no empty string", "Excel", "isp IS NOT NULL", "isp <> trim(isp) OR isp = ''", "trim + ''->NULL"),
    ("isp / asn", "Null together (isp null iff asn null)", "Excel", "true", "(isp IS NULL) <> (asn IS NULL)", "None needed"),
    ("asn", "Pattern 'AS' + digits", "Excel", "asn IS NOT NULL", "NOT regexp_matches(asn, '^AS\\d+$')", "upper() + trim"),
    ("os", "Trimmed, no empty string", "Added", "os IS NOT NULL", "os <> trim(os) OR os = ''", "trim + ''->NULL"),
    ("product", "Trimmed, no empty string", "Excel", "product IS NOT NULL", "product <> trim(product) OR product = ''", "trim + ''->NULL"),
    ("product_version", "Trimmed, no empty string", "Excel", "product_version IS NOT NULL",
     "product_version <> trim(product_version) OR product_version = ''", "trim + ''->NULL"),
    ("devicetype", "Trimmed, no empty string", "Excel", "devicetype IS NOT NULL", "devicetype <> trim(devicetype) OR devicetype = ''", "trim + ''->NULL"),
    ("tags", "Empty list stored instead of NULL", "Excel", "true", "tags IS NOT NULL AND len(tags) = 0", "Empty list -> NULL"),
    ("is_* flags (7)", "Boolean flags never NULL", "Added", "true",
     " OR ".join(f"{f} IS NULL" for f in FLAGS),
     "NULL -> false (list_contains(NULL, ..) returned NULL when tags was NULL)"),
    ("tags / cloud", "'cloud' tag implies cloud_provider populated", "Excel", "coalesce(list_contains(tags, 'cloud'), false)",
     "cloud_provider IS NULL", "Flag only"),
    ("cpe23", "Every element starts with 'cpe:2.3:'", "Excel", "len(cpe23) > 0",
     "list_bool_or(list_transform(cpe23, x -> NOT starts_with(x, 'cpe:2.3:')))", "None needed"),
    ("cpe23", "Empty list stored instead of NULL", "Excel", "true", "cpe23 IS NOT NULL AND len(cpe23) = 0", "Empty list -> NULL"),
    ("vulns", "CVE keys valid (CVE-yyyy-nnnn or MS bulletin)", "Added", "cardinality(vulns) > 0",
     f"list_bool_or(list_transform(map_keys(vulns), k -> NOT regexp_matches(k, '{CVE_RE}')))", "Drop malformed keys"),
    ("vulns", "CVSS within 0-10", "Added", "cardinality(vulns) > 0",
     "list_bool_or(list_transform(map_values(vulns), v -> v.cvss < 0 OR v.cvss > 10))", "None needed"),
    ("vulns", "EPSS within 0-1", "Added", "cardinality(vulns) > 0",
     "list_bool_or(list_transform(map_values(vulns), v -> v.epss < 0 OR v.epss > 1))", "None needed"),
    ("vulns", "Empty map stored instead of NULL", "Excel", "true", "vulns IS NOT NULL AND cardinality(vulns) = 0", "Empty map -> NULL"),
    ("country_code", "Not null", "Excel", "true", "country_code IS NULL", "None possible (value unknown)"),
    ("country_code", "Exactly 2 uppercase letters", "Excel", "country_code IS NOT NULL", "NOT regexp_matches(country_code, '^[A-Z]{2}$')", "upper() + trim"),
    ("country_name", "Trimmed, no empty string", "Excel", "country_name IS NOT NULL", "country_name <> trim(country_name) OR country_name = ''", "trim + ''->NULL"),
    ("city", "Trimmed, no empty string", "Excel", "city IS NOT NULL", "city <> trim(city) OR city = ''", "trim + ''->NULL"),
    ("latitude", "Between -90 and 90", "Excel", "latitude IS NOT NULL", "latitude < -90 OR latitude > 90", "None needed"),
    ("longitude", "Between -180 and 180", "Excel", "longitude IS NOT NULL", "longitude < -180 OR longitude > 180", "None needed"),
    ("region_code", "No empty string (normalise '' to NULL)", "Excel", "region_code IS NOT NULL", "region_code = '' OR region_code <> trim(region_code)", "trim + ''->NULL"),
    ("cloud_provider", "Trimmed, no empty string", "Added", "cloud_provider IS NOT NULL", "cloud_provider = '' OR cloud_provider <> trim(cloud_provider)", "trim + ''->NULL"),
    ("ssl_jarm", "62 lowercase hex characters", "Added", "ssl_jarm IS NOT NULL", "NOT regexp_matches(ssl_jarm, '^[0-9a-f]{62}$')", "None needed"),
    ("ssl_expired / ssl_expires", "expired flag agrees with expiry date vs scan time", "Added",
     "ssl_expired IS NOT NULL AND ssl_expires IS NOT NULL", "ssl_expired <> (ssl_expires < ts)", "None needed"),
    ("ssl_subject_cn / org", "Trimmed, no empty string", "Added", "ssl_subject_cn IS NOT NULL OR ssl_subject_org IS NOT NULL",
     "ssl_subject_cn = '' OR ssl_subject_cn <> trim(ssl_subject_cn) OR ssl_subject_org = '' OR ssl_subject_org <> trim(ssl_subject_org)", "trim + ''->NULL"),
    ("http_status", "Integer 100-599", "Excel", "http_status IS NOT NULL", "http_status < 100 OR http_status > 599", "Out-of-range -> NULL"),
    ("http_server", "Trimmed, no empty string", "Excel", "http_server IS NOT NULL", "http_server = '' OR http_server <> trim(http_server)", "trim + ''->NULL"),
    ("http_title", "Trimmed, no empty string", "Excel", "http_title IS NOT NULL", "http_title = '' OR http_title <> trim(http_title)", "trim + ''->NULL"),
    ("shodan_module", "Not null", "Excel", "true", "shodan_module IS NULL", "None needed"),
    ("screenshot_labels", "Non-empty list when present", "Excel", "screenshot_labels IS NOT NULL", "len(screenshot_labels) = 0", "Empty list -> NULL"),
    ("screenshot_text", "Trimmed, no empty string", "Added", "screenshot_text IS NOT NULL", "screenshot_text = '' OR screenshot_text <> trim(screenshot_text)", "trim + ''->NULL"),
]

# Scalar (cross-row) checks: (field, check, source, sql_template using {t}, remediation)
SCALAR = [
    ("ip_str + port + ts", "No duplicate observation rows", "Excel",
     "SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY ip_str, port, ts HAVING count(*) > 1)",
     "Duplicates were all rows with NULL ip_str colliding; removed with the quarantine"),
    ("country_code / country_name", "1:1 mapping (one name per code, one code per name)", "Excel",
     "SELECT (SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY country_code HAVING count(DISTINCT country_name) > 1)) + "
     "(SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY country_name HAVING count(DISTINCT country_code) > 1))",
     "None needed"),
]

NOT_RUN = [
    ("ip", "ip equals integer form of ip_str", "Field not loaded into Silver (numeric duplicate of ip_str)"),
    ("hash / data", "hash = 0 exactly when data is null", "Neither field loaded (banner dropped; hash is engineering-only)"),
    ("cpe / cpe23_hash", "cpe23 null iff cpe null; hash null iff cpe23 null", "cpe (deprecated) and cpe23_hash not loaded"),
    ("http.status vs data", "http.status matches first line of data banner", "Raw banner 'data' not loaded"),
    ("_shodan.id", "Unique per row; 36 chars with 4 hyphens", "Sub-field not loaded (internal metadata)"),
    ("_shodan.crawler", "40 hex characters", "Sub-field not loaded"),
    ("_shodan.region / ptr / options", "'' -> NULL; ptr TRUE/NULL; options = {}", "Sub-fields not loaded"),
    ("http.host", "Equals ip_str", "Sub-field not loaded"),
    ("http.headers", "Keys lowercase", "Sub-field not loaded (dropped as bulky)"),
    ("http.html + *_hash", "'' vs NULL consistency; hash 0.0 when html empty", "html and hashes not loaded (see dropped_fields.csv)"),
    ("title / html (top-level)", "Deprecated top-level fields vs http.title/html", "Deprecated; not loaded"),
    ("screenshot.mime / data", "mime starts with 'image/'", "Sub-fields not loaded; labels and text only"),
    ("location.area_code / postal_code / dma_code", "100% null - confirm on larger data", "Not loaded; profile shows no value to keep"),
    ("any text cell", "Cell exceeds Excel 32,767 chars", "Not applicable: Silver is parquet in DuckDB, not Excel"),
    ("os, version, devicetype, ssl, vulns", "Only % null and type checks defined", "Covered by the null profile and type checks above"),
]


def counts(con, rel):
    sel = ["count(*)"]
    for _, _, _, scope, bad, _ in CHECKS:
        sel.append(f"count(*) FILTER (WHERE {scope})")
        sel.append(f"count(*) FILTER (WHERE ({scope}) AND ({bad}))")
    return con.execute(f"SELECT {', '.join(sel)} FROM {rel}").fetchone()


def pct(n, d):
    return round(n / d * 100, 3) if d else 0.0


def status(before, after, fix=""):
    if before == 0:
        return "Pass"
    if after == 0:
        return "Fixed by remediation"
    if fix.startswith(("None possible", "Flag only")):
        return "Open - flagged, not auto-fixed"  # residual drop is only the quarantined rows
    return "Partly fixed"


def main():
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PATH}')")
    con.execute(CLEAN_SQL)
    b, a = counts(con, "h"), counts(con, "c")
    total_b, total_a = b[0], a[0]
    print(f"rows before {total_b:,}  after {total_a:,}  quarantined {total_b - total_a:,}")

    rows = []
    for i, (field, check, src, _scope, _bad, fix) in enumerate(CHECKS):
        sb, vb = b[1 + 2 * i], b[2 + 2 * i]
        sa, va = a[1 + 2 * i], a[2 + 2 * i]
        rows.append([field, check, src, sb, vb, pct(vb, sb), fix, sa, va, pct(va, sa), status(vb, va, fix)])
    for field, check, src, tmpl, fix in SCALAR:
        vb = con.execute(tmpl.format(t="h")).fetchone()[0]
        va = con.execute(tmpl.format(t="c")).fetchone()[0]
        rows.append([field, check, src, total_b, vb, pct(vb, total_b), fix, total_a, va, pct(va, total_a), status(vb, va, fix)])
    for field, check, why in NOT_RUN:
        rows.append([field, check, "Excel", "", "", "", why, "", "", "", "Not run"])

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["field", "check", "source", "rows_in_scope_before", "violations_before", "pct_before",
                    "remediation_or_note", "rows_in_scope_after", "violations_after", "pct_after", "status"])
        w.writerows(rows)

    cols = [r[0] for r in con.execute("DESCRIBE SELECT * FROM h").fetchall()]
    nb = con.execute("SELECT " + ", ".join(f"count(*) FILTER (WHERE {c} IS NULL)" for c in cols) + " FROM h").fetchone()
    na = con.execute("SELECT " + ", ".join(f"count(*) FILTER (WHERE {c} IS NULL)" for c in cols) + " FROM c").fetchone()
    with open(NULLS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["column", "null_before", "null_pct_before", "null_after", "null_pct_after"])
        for c, x, y in zip(cols, nb, na):
            w.writerow([c, x, pct(x, total_b), y, pct(y, total_a)])

    from collections import Counter
    print(Counter(r[-1] for r in rows))
    print(f"Wrote {REPORT}\nWrote {NULLS}")


if __name__ == "__main__":
    main()
