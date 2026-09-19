"""JSONL trace log for every LLM call. Schema matches docs/ARCHITECTURE.md's
"Tracing schema" section - one line per call, in evals/results/traces.jsonl.

Cost figures below are approximate list pricing and MUST be verified against
Anthropic's current pricing page before being relied on for real budgeting -
they are not fetched live and can drift out of date.
"""
import json
import os
import time
import uuid

TRACE_PATH = os.path.join(os.path.dirname(__file__), "..", "evals", "results", "traces.jsonl")

# USD per million tokens: (input_rate, output_rate). Approximate - verify
# against https://www.anthropic.com/pricing before trusting for real budgeting.
MODEL_PRICING = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-opus-5": (15.00, 75.00),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = MODEL_PRICING.get(model, (0.0, 0.0))
    return round((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 6)


def log_trace(
    *,
    skill: str,
    prompt_version: str,
    model: str,
    request: dict,
    response: dict,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    decision: str,
    call_id: str | None = None,
) -> dict:
    """Append one trace record and return it. Never raises on the caller's
    behalf for a logging failure being silent would hide real usage - if
    this fails, the caller should know."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "call_id": call_id or str(uuid.uuid4()),
        "skill": skill,
        "prompt_version": prompt_version,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": round(latency_ms, 1),
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        "request": request,
        "response": response,
        "decision": decision,
    }
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    with open(TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_traces(skill: str | None = None) -> list[dict]:
    """Read back all trace records, optionally filtered by skill name."""
    if not os.path.exists(TRACE_PATH):
        return []
    records = []
    with open(TRACE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if skill is None or rec.get("skill") == skill:
                records.append(rec)
    return records
