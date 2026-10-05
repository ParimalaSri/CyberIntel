"""Adjudicates the 'review' band (50 <= contact_score <= 84) with an LLM,
per skills/account-scoring/SKILL.md. `pipeline/gold/scoring.py` only computes
the rule-based band; this script is the "Sprint 4" step that turns review-band
rows into a final contact/skip/escalate decision, using the same prompt
(prompts/account_scoring/v1.md) already validated by evals/run_eval.py.

Writes data/gold/accounts_adjudicated.parquet: every row from accounts.parquet
plus final_label, rationale, and confidence. Rows outside the review band pass
through untouched (final_label = band, rationale/confidence = null) - rules
decided those for free, nothing an LLM adds there.

Usage:
  python pipeline/gold/adjudicate.py --sample 50   # test run, no full cost
  python pipeline/gold/adjudicate.py               # full run, all review rows
Requires GROQ_API_KEY in the environment (or .env at repo root).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv  # noqa: E402
import duckdb  # noqa: E402
from llm import prompts  # noqa: E402
from llm.tracing import log_trace, new_run_id  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

ACCOUNTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold", "accounts.parquet")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold", "accounts_adjudicated.parquet")
# Pinned on purpose: a new prompt version is promoted by changing this (or passing
# --prompt-version) after it beats the current one in evals/compare_prompts.py.
DEFAULT_PROMPT_VERSION = "v1"
MODEL = "openai/gpt-oss-120b"  # same judgment model evals/run_eval.py validated this prompt against

INPUT_FIELDS = [
    "company_key", "resolution_tier", "primary_country_code", "host_count",
    "fit_score", "urgency_score", "contact_score", "cve_count", "max_cvss",
    "max_epss", "eol_count", "self_signed_count", "risky_open_port_count",
    "c2_count", "has_screenshot_evidence", "why",
]


def call_model(client, prompt_template: str, account_input: dict) -> tuple[dict, dict, float]:
    import groq

    filled = prompt_template.replace("{{account_json}}", json.dumps(account_input))
    t0 = time.time()
    for attempt in range(5):
        try:
            msg = client.chat.completions.create(
                model=MODEL,
                max_tokens=1536,
                messages=[{"role": "user", "content": filled}],
            )
            break
        except groq.RateLimitError:
            wait = 8 * (attempt + 1)
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
    else:
        raise RuntimeError("rate limited after 5 retries")
    latency_ms = (time.time() - t0) * 1000
    text = msg.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"label": "escalate", "rationale": f"unparseable response: {text[:200]}", "confidence": "low"}
    usage = {"input_tokens": msg.usage.prompt_tokens, "output_tokens": msg.usage.completion_tokens}
    return parsed, usage, latency_ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None, help="Adjudicate only N review-band rows (for testing)")
    ap.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION,
                    help="prompt version under prompts/account_scoring/ (default: %(default)s)")
    args = ap.parse_args()

    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set. Set it in your environment and re-run.", file=sys.stderr)
        sys.exit(1)

    from groq import Groq
    client = Groq()

    con = duckdb.connect()
    review_rows = con.execute(f"""
        SELECT {", ".join(INPUT_FIELDS)} FROM read_parquet('{ACCOUNTS_PATH}')
        WHERE band = 'review'
        {f"ORDER BY random() LIMIT {args.sample}" if args.sample else ""}
    """).fetchall()
    columns = [d[0] for d in con.description]
    prompt = prompts.load("account-scoring", args.prompt_version)
    prompt_template = prompt.text
    run_id = new_run_id()

    print(f"Adjudicating {len(review_rows)} review-band accounts "
          f"(prompt {prompt.version} [{prompt.hash}], {MODEL}, run {run_id})...")
    results = []
    total_cost = 0.0
    for row in review_rows:
        account_input = dict(zip(columns, row))
        parsed, usage, latency_ms = call_model(client, prompt_template, account_input)
        label = parsed.get("label", "escalate")
        rec = log_trace(
            skill="account-scoring",
            prompt_version=prompt.version,
            model=MODEL,
            request={"company_key": account_input["company_key"], "input": account_input},
            response=parsed,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
            decision=label,
            run_id=run_id,
            prompt_hash=prompt.hash,
        )
        total_cost += rec["cost_usd"]
        results.append({
            "company_key": account_input["company_key"],
            "final_label": label,
            "rationale": parsed.get("rationale"),
            "confidence": parsed.get("confidence"),
        })
        print(f"  {account_input['company_key']:35s} contact_score={account_input['contact_score']:.0f} -> {label}")

    print(f"\nTotal cost: ${total_cost:.4f}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    con.execute("CREATE TEMP TABLE adjudications AS SELECT * FROM (VALUES "
                + ",".join("(?, ?, ?, ?)" for _ in results) + ") AS t(company_key, final_label, rationale, confidence)",
                [v for r in results for v in (r["company_key"], r["final_label"], r["rationale"], r["confidence"])]
                if results else [])
    if not results:
        con.execute("CREATE TEMP TABLE adjudications AS SELECT NULL::VARCHAR AS company_key, "
                     "NULL::VARCHAR AS final_label, NULL::VARCHAR AS rationale, NULL::VARCHAR AS confidence WHERE FALSE")

    con.execute(f"""
        COPY (
            SELECT a.*,
                coalesce(j.final_label, a.band) AS final_label,
                j.rationale,
                j.confidence
            FROM read_parquet('{ACCOUNTS_PATH}') a
            LEFT JOIN adjudications j USING (company_key)
        ) TO '{OUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n = con.execute(f"SELECT count(*) FROM read_parquet('{OUT_PATH}')").fetchone()[0]
    print(f"\nWrote {OUT_PATH} ({n:,} rows)")
    if args.sample:
        print(f"This was a --sample {args.sample} test run - only {len(results)} review rows were "
              "actually adjudicated; the rest of the review band still shows its rule band as final_label. "
              "Run without --sample for a full pass.")


if __name__ == "__main__":
    main()
