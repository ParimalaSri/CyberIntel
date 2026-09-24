"""Minimal chatbot over the Gold layer only - tool-calling, not RAG over
raw data. Small model (gpt-oss-20b, not the 120b judgment model) since this
is retrieval, not reasoning - keeps tokens/cost down per the cost-model
guidance (cheap model for lookup tasks, strong model reserved for the
account-scoring skill).

Usage: python app/chatbot.py
Requires GROQ_API_KEY in .env.
"""
import json
import os
import sys

import duckdb
from dotenv import load_dotenv
from groq import Groq

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_ROOT = os.path.join(os.path.dirname(__file__), "..")
ACCOUNTS = os.path.join(_ROOT, "data", "gold", "accounts.parquet")
CONTACTS = os.path.join(_ROOT, "data", "gold", "contacts.jsonl")
MODEL = "openai/gpt-oss-20b"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_accounts",
            "description": "List companies from the Gold layer, filtered and sorted by score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "band": {"type": "string", "enum": ["contact", "review", "skip", "exclude"]},
                    "country": {"type": "string", "description": "2-letter country code"},
                    "min_score": {"type": "number"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account",
            "description": "Get full detail for one company by its company_key.",
            "parameters": {
                "type": "object",
                "properties": {"company_key": {"type": "string"}},
                "required": ["company_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_contact",
            "description": "Get the enriched contact (name/email/title) found via Snov.io for a company. Only available for the top 25 contact-band accounts.",
            "parameters": {
                "type": "object",
                "properties": {"company_key": {"type": "string"}},
                "required": ["company_key"],
            },
        },
    },
]


def query_accounts(band=None, country=None, min_score=None, limit=10):
    con = duckdb.connect()
    where = []
    if band:
        where.append(f"band = '{band}'")
    if country:
        where.append(f"primary_country_code = '{country.upper()}'")
    if min_score is not None:
        where.append(f"contact_score >= {min_score}")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, band
        FROM read_parquet('{ACCOUNTS}') {clause}
        ORDER BY contact_score DESC LIMIT {limit}
    """).fetchall()
    return [{"company_key": r[0], "country": r[1], "score": r[2], "band": r[3]} for r in rows]


def get_account(company_key):
    con = duckdb.connect()
    row = con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, band, why
        FROM read_parquet('{ACCOUNTS}') WHERE company_key = '{company_key}'
    """).fetchone()
    if not row:
        return {"error": "not found"}
    return {"company_key": row[0], "country": row[1], "score": row[2], "band": row[3], "why": row[4]}


def get_contact(company_key):
    if not os.path.exists(CONTACTS):
        return {"error": "no enrichment data yet"}
    with open(CONTACTS, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["company_key"] == company_key:
                if not rec.get("contact"):
                    return {"error": "no contact found for this company"}
                return rec["contact"]
    return {"error": "not in the enriched (top 25) set"}


DISPATCH = {"query_accounts": query_accounts, "get_account": get_account, "get_contact": get_contact}

SYSTEM = (
    "You answer questions about a cybersecurity sales-lead dataset using only "
    "the provided tools. When asked for contact details (email, who to reach), "
    "use get_contact, not get_account. Never invent company names, numbers, "
    "or emails - if a tool returns no data, say so. Keep answers short."
)


def chat(client, messages):
    resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, max_tokens=500)
    msg = resp.choices[0].message
    if msg.tool_calls:
        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = DISPATCH[tc.function.name](**args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
        return chat(client, messages)
    return msg.content


def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set.", file=sys.stderr)
        sys.exit(1)
    client = Groq()
    print("Gold layer chatbot. Ask about accounts (e.g. 'top 5 contact accounts in Germany'). Ctrl+C to exit.")
    history = [{"role": "system", "content": SYSTEM}]
    while True:
        q = input("\n> ")
        history.append({"role": "user", "content": q})
        answer = chat(client, history)
        history.append({"role": "assistant", "content": answer})
        print(answer)


if __name__ == "__main__":
    main()
