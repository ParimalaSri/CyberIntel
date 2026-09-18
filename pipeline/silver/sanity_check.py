"""Null-rate / distinct-count sanity report for silver_hosts.parquet."""
import duckdb

PATH = r"D:\Cyber_DataSet\data\silver\hosts.parquet"

NULL_RATE_COLUMNS = [
    "ip_str", "port", "transport", "ts", "hostnames", "domains",
    "country_code", "city", "org", "isp", "asn", "os", "product",
    "devicetype", "tags", "cpe23", "vulns", "ssl_jarm", "http_status",
    "http_server", "http_title", "shodan_module",
]

DISTINCT_COLUMNS = ["country_code", "org", "isp", "asn", "product", "port"]


def main():
    con = duckdb.connect()
    total = con.execute(f"SELECT count(*) FROM read_parquet('{PATH}')").fetchone()[0]
    print(f"Total rows: {total:,}\n")

    print("--- Null / empty rate per column ---")
    for col in NULL_RATE_COLUMNS:
        null_count = con.execute(
            f"SELECT count(*) FROM read_parquet('{PATH}') WHERE {col} IS NULL"
        ).fetchone()[0]
        print(f"{col:16s} {null_count:>10,} null ({null_count/total*100:5.1f}%)")

    print("\n--- Distinct counts ---")
    for col in DISTINCT_COLUMNS:
        d = con.execute(
            f"SELECT count(DISTINCT {col}) FROM read_parquet('{PATH}')"
        ).fetchone()[0]
        print(f"{col:16s} {d:>10,} distinct")

    print("\n--- Boolean tag flags (true rate) ---")
    for col in ["is_eol_product", "is_self_signed", "is_honeypot", "is_c2", "is_iot", "is_vpn", "is_database_tag"]:
        c = con.execute(
            f"SELECT count(*) FROM read_parquet('{PATH}') WHERE {col} = true"
        ).fetchone()[0]
        print(f"{col:16s} {c:>10,} ({c/total*100:5.2f}%)")

    print("\n--- Rows with at least one CVE ---")
    c = con.execute(
        f"SELECT count(*) FROM read_parquet('{PATH}') WHERE vulns IS NOT NULL AND cardinality(vulns) > 0"
    ).fetchone()[0]
    print(f"has_vulns: {c:,} ({c/total*100:.2f}%)")

    print("\n--- Rows with a usable domain (non-empty domains array) ---")
    c = con.execute(
        f"SELECT count(*) FROM read_parquet('{PATH}') WHERE domains IS NOT NULL AND len(domains) > 0"
    ).fetchone()[0]
    print(f"has_domains: {c:,} ({c/total*100:.2f}%)")


if __name__ == "__main__":
    main()
