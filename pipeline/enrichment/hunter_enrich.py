"""Contact enrichment via Hunter.io Domain Search - contact band only,
domain-tier only (org-tier has no real domain to look up), top N by
contact_score to fit the free-tier quota (25 lookups/month).

Requires HUNTER_API_KEY in .env or the environment.
Usage: python pipeline/enrichment/hunter_enrich.py [--limit 25]
"""
import argparse
import json
import os
import sys
import time

import duckdb
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

ACCOUNTS_PATH = r"D:\Cyber_DataSet\data\gold\accounts.parquet"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold")
OUT_PATH = os.path.join(OUT_DIR, "contacts.jsonl")
HUNTER_URL = "https://api.hunter.io/v2/domain-search"


def pick_top_accounts(limit: int) -> list[dict]:
    con = duckdb.connect()
    res = con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, why
        FROM read_parquet('{ACCOUNTS_PATH}')
        WHERE band = 'contact' AND resolution_tier = 'domain'
        ORDER BY contact_score DESC
        LIMIT {limit}
    """)
    cols = [d[0] for d in res.description]
    return [dict(zip(cols, row)) for row in res.fetchall()]


def lookup_domain(api_key: str, domain: str) -> dict:
    resp = requests.get(HUNTER_URL, params={"domain": domain, "api_key": api_key}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def best_contact(hunter_data: dict) -> dict | None:
    emails = (hunter_data.get("data") or {}).get("emails") or []
    if not emails:
        return None
    # prefer a security/IT/admin-titled contact if one exists, else highest-confidence
    security_kw = ("security", "it ", "admin", "cto", "cio", "sysadmin", "devops")
    for e in sorted(emails, key=lambda x: x.get("confidence", 0), reverse=True):
        position = (e.get("position") or "").lower()
        if any(k in position for k in security_kw):
            return e
    return max(emails, key=lambda x: x.get("confidence", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    api_key = os.environ.get("HUNTER_API_KEY")
    if not api_key:
        print("HUNTER_API_KEY not set. Add it to .env and re-run.", file=sys.stderr)
        sys.exit(1)

    accounts = pick_top_accounts(args.limit)
    print(f"Enriching top {len(accounts)} contact-band, domain-tier accounts...")

    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for acct in accounts:
            domain = acct["company_key"]
            try:
                data = lookup_domain(api_key, domain)
                contact = best_contact(data)
            except requests.HTTPError as e:
                print(f"  [ERROR] {domain}: {e}")
                contact = None
                data = {"error": str(e)}

            record = {
                "company_key": domain,
                "contact_score": acct["contact_score"],
                "country": acct["primary_country_code"],
                "hunter_organization": (data.get("data") or {}).get("organization") if isinstance(data, dict) else None,
                "contact": contact,
                "raw_email_count": len((data.get("data") or {}).get("emails") or []) if isinstance(data, dict) else 0,
            }
            results.append(record)
            f.write(json.dumps(record) + "\n")

            if contact:
                print(f"  [OK] {domain:35s} -> {contact.get('value')} ({contact.get('position') or 'unknown title'}, confidence {contact.get('confidence')})")
            else:
                print(f"  [NONE] {domain:35s} -> no contacts found")

            time.sleep(1)  # be polite to the free tier

    n_found = sum(1 for r in results if r["contact"])
    print(f"\nEnriched {len(results)} accounts, contacts found for {n_found}. Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
