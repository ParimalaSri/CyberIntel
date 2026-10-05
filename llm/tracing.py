"""JSONL trace log for every LLM call. Schema matches docs/ARCHITECTURE.md's
"Tracing schema" section - one line per call, in evals/results/traces.jsonl.

Schema v2 adds, on top of the original fields: run_id (group the calls of one
batch/eval run), status + error + attempts (so a failed or unparseable call is
never mistaken for a real decision), prompt_hash + input_hash (tie a decision
to the exact prompt text and input), and billing_tier (is `cost_usd` a real
charge or only a list-price estimate). Old v1 lines are still readable:
read_traces() fills the missing fields in.

Cost figures below are approximate list pricing and MUST be verified against
each provider's current pricing page before being relied on for real
budgeting - they are not fetched live and can drift out of date.
"""
import hashlib
import json
import os
import time
import uuid

TRACE_PATH = os.path.join(os.path.dirname(__file__), "..", "evals", "results", "traces.jsonl")
SCHEMA_VERSION = 2

# status values: a call that did not yield a usable decision must not look like one
STATUS_OK = "ok"
STATUS_PARSE_ERROR = "parse_error"      # model replied, output was not valid JSON
STATUS_ERROR = "error"                  # request failed (connection, API error, retries exhausted)
STATUS_RATE_LIMITED = "rate_limited"    # gave up after rate-limit retries
STATUSES = (STATUS_OK, STATUS_PARSE_ERROR, STATUS_ERROR, STATUS_RATE_LIMITED)

# USD per million tokens: (input_rate, output_rate). Approximate - verify
# against each provider's pricing page before trusting for real budgeting.
MODEL_PRICING = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-opus-5": (15.00, 75.00),
    # Groq free tier = $0 actual cost. Paid-tier rate shown here for
    # cost-model realism if usage ever exceeds the free tier - verify
    # against https://groq.com/pricing before trusting it.
    "openai/gpt-oss-120b": (0.15, 0.75),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = MODEL_PRICING.get(model, (0.0, 0.0))
    return round((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 6)


def new_run_id() -> str:
    """One id per batch/eval run, so a report can group (and compare) runs."""
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def text_hash(text: str) -> str:
    """Short content hash - identifies the exact prompt text that was used."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def input_hash(request: dict) -> str:
    return text_hash(json.dumps(request, sort_keys=True, default=str))


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
    run_id: str | None = None,
    status: str = STATUS_OK,
    error: str | None = None,
    attempts: int = 1,
    prompt_hash: str | None = None,
) -> dict:
    """Append one trace record and return it. Never raises on the caller's
    behalf for a logging failure being silent would hide real usage - if
    this fails, the caller should know."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    record = {
        "schema_version": SCHEMA_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "call_id": call_id or str(uuid.uuid4()),
        "run_id": run_id,
        "skill": skill,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "model": model,
        "status": status,
        "error": error,
        "attempts": attempts,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": round(latency_ms, 1),
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        # free | paid | unknown. cost_usd is a LIST-PRICE ESTIMATE; on a free
        # tier nothing is actually charged. Set LLM_BILLING_TIER to record it.
        "billing_tier": os.environ.get("LLM_BILLING_TIER", "unknown"),
        "input_hash": input_hash(request),
        "request": request,
        "response": response,
        "decision": decision,
    }
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    with open(TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def log_failure(
    *,
    skill: str,
    prompt_version: str,
    model: str,
    request: dict,
    error: str,
    latency_ms: float = 0.0,
    attempts: int = 1,
    status: str = STATUS_ERROR,
    run_id: str | None = None,
    prompt_hash: str | None = None,
) -> dict:
    """Record a call that produced no decision (no tokens, decision=None)."""
    return log_trace(
        skill=skill, prompt_version=prompt_version, model=model, request=request,
        response={}, input_tokens=0, output_tokens=0, latency_ms=latency_ms,
        decision=None, status=status, error=error, attempts=attempts,
        run_id=run_id, prompt_hash=prompt_hash,
    )


def normalize(rec: dict) -> dict:
    """Fill schema-v2 fields on an old (v1) record, without mutating the file.

    Early v1 runs logged an unparseable model reply as an ordinary `escalate`
    decision; those are recognised by their rationale and reclassified as
    parse_error so they don't pass for real escalations.
    """
    rec = dict(rec)
    rec.setdefault("schema_version", 1)
    rec.setdefault("run_id", None)
    rec.setdefault("error", None)
    rec.setdefault("attempts", 1)
    rec.setdefault("prompt_hash", None)
    rec.setdefault("billing_tier", "unknown")
    rec.setdefault("input_hash", None)
    if "status" not in rec:
        rationale = str((rec.get("response") or {}).get("rationale", ""))
        rec["status"] = STATUS_PARSE_ERROR if rationale.startswith("unparseable response") else STATUS_OK
    return rec


def read_traces(skill: str | None = None, normalized: bool = True) -> list[dict]:
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
                records.append(normalize(rec) if normalized else rec)
    return records
