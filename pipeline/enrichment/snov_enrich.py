"""Contact enrichment via Snov.io Domain Search - contact band only,
domain-tier only (org-tier has no real domain to look up), top N by
contact_score to fit the free-tier quota.

Requires SNOV_CLIENT_ID and SNOV_CLIENT_SECRET in .env or the environment
(Dashboard -> API on snov.io - OAuth2 client-credentials, not a single key).

NOTE: Snov.io's API endpoints/response shape below are built from general
knowledge, not a live-verified spec - if a call 404s or the JSON shape looks
off, check https://snov.io/api against the actual response before assuming
the script's logic is wrong.

Usage: python pipeline/enrichment/snov_enrich.py [--limit 25]
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
TOKEN_URL = "https://api.snov.io/v1/oauth/access_token"
DOMAIN_SEARCH_URL = "https://api.snov.io/v2/domain-emails-with-info"


def get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()["access_token"]


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


def lookup_domain(access_token: str, domain: str) -> dict:
    resp = requests.get(DOMAIN_SEARCH_URL, params={
        "access_token": access_token, "domain": domain, "type": "all", "limit": 20,
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()


def best_contact(snov_data: dict) -> dict | None:
    emails = snov_data.get("emails") or []
    if not emails:
        return None
    security_kw = ("security", "it ", "admin", "cto", "cio", "sysadmin", "devops")
    for e in sorted(emails, key=lambda x: x.get("confidence", x.get("smtp_status_percentage", 0)), reverse=True):
        position = (e.get("position") or "").lower()
        if any(k in position for k in security_kw):
            return e
    return max(emails, key=lambda x: x.get("confidence", x.get("smtp_status_percentage", 0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    client_id = os.environ.get("SNOV_CLIENT_ID")
    client_secret = os.environ.get("SNOV_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("SNOV_CLIENT_ID / SNOV_CLIENT_SECRET not set. Add them to .env and re-run.", file=sys.stderr)
        sys.exit(1)

    token = get_access_token(client_id, client_secret)

    accounts = pick_top_accounts(args.limit)
    print(f"Enriching top {len(accounts)} contact-band, domain-tier accounts...")

    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for acct in accounts:
            domain = acct["company_key"]
            try:
                data = lookup_domain(token, domain)
                contact = best_contact(data)
            except requests.HTTPError as e:
                print(f"  [ERROR] {domain}: {e}")
                contact = None
                data = {"error": str(e)}

            record = {
                "company_key": domain,
                "contact_score": acct["contact_score"],
                "country": acct["primary_country_code"],
                "contact": contact,
                "raw_email_count": len(data.get("emails") or []) if isinstance(data, dict) else 0,
            }
            results.append(record)
            f.write(json.dumps(record) + "\n")

            if contact:
                print(f"  [OK] {domain:35s} -> {contact.get('email')} ({contact.get('position') or 'unknown title'})")
            else:
                print(f"  [NONE] {domain:35s} -> no contacts found")

            time.sleep(1)

    n_found = sum(1 for r in results if r["contact"])
    print(f"\nEnriched {len(results)} accounts, contacts found for {n_found}. Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
