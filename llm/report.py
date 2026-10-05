"""Summarise the LLM trace log: usage, cost, latency, decisions and failures.

Usage:
  python llm/report.py                       # everything in the trace log
  python llm/report.py --skill account-scoring
  python llm/report.py --since 2026-10-01

Reads evals/results/traces.jsonl only - it never calls a model, so it is free
to run. `est cost` is a list-price estimate (see llm/tracing.py); it is only
a real charge when billing_tier is "paid".
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm.tracing import read_traces, TRACE_PATH  # noqa: E402


def pctl(values, p):
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def fmt_s(ms):
    return "-" if ms is None else f"{ms / 1000:.1f}s"


def summarise(recs):
    ok = [r for r in recs if r["status"] == "ok"]
    lat = [r["latency_ms"] for r in ok if r["latency_ms"]]
    return {
        "calls": len(recs),
        "ok": len(ok),
        "failed": len(recs) - len(ok),
        "in_tok": sum(r["input_tokens"] for r in recs),
        "out_tok": sum(r["output_tokens"] for r in recs),
        "avg_in": (sum(r["input_tokens"] for r in ok) / len(ok)) if ok else 0,
        "avg_out": (sum(r["output_tokens"] for r in ok) / len(ok)) if ok else 0,
        "p50": pctl(lat, 50),
        "p95": pctl(lat, 95),
        "cost": sum(r["cost_usd"] for r in recs),
        "decisions": collections.Counter(r["decision"] for r in ok),
        "statuses": collections.Counter(r["status"] for r in recs),
        "tiers": collections.Counter(r["billing_tier"] for r in recs),
    }


def print_group(title, recs):
    s = summarise(recs)
    print(f"\n{title}")
    print(f"  calls {s['calls']:,}  ok {s['ok']:,}  failed/unusable {s['failed']:,} "
          f"({s['failed'] / s['calls'] * 100:.1f}%)")
    print(f"  tokens in {s['in_tok']:,} / out {s['out_tok']:,}   "
          f"avg per ok call: {s['avg_in']:.0f} in / {s['avg_out']:.0f} out")
    print(f"  latency p50 {fmt_s(s['p50'])}  p95 {fmt_s(s['p95'])}")
    print(f"  est cost ${s['cost']:.4f}   billing tier: {dict(s['tiers'])}")
    print(f"  decisions (ok calls only): {dict(s['decisions'])}")
    if s["failed"]:
        print(f"  non-ok statuses: { {k: v for k, v in s['statuses'].items() if k != 'ok'} }")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill")
    ap.add_argument("--since", help="YYYY-MM-DD, inclusive")
    args = ap.parse_args()

    recs = read_traces(args.skill)
    if args.since:
        recs = [r for r in recs if r["ts"][:10] >= args.since]
    if not recs:
        print(f"No traces found in {os.path.abspath(TRACE_PATH)}")
        return

    print(f"LLM trace report - {len(recs):,} calls, {recs[0]['ts'][:10]} to {recs[-1]['ts'][:10]}")
    print_group("ALL CALLS", recs)

    groups = collections.defaultdict(list)
    for r in recs:
        groups[(r["skill"], r["prompt_version"], r["model"])].append(r)
    for (skill, pv, model), rs in sorted(groups.items()):
        print_group(f"{skill} | prompt {pv} | {model}", rs)

    print("\nPER DAY")
    by_day = collections.defaultdict(list)
    for r in recs:
        by_day[r["ts"][:10]].append(r)
    print(f"  {'day':10s} {'calls':>6s} {'failed':>7s} {'tokens':>10s} {'est $':>9s}")
    for day, rs in sorted(by_day.items()):
        s = summarise(rs)
        print(f"  {day:10s} {s['calls']:>6,} {s['failed']:>7,} {s['in_tok'] + s['out_tok']:>10,} {s['cost']:>9.4f}")

    runs = collections.defaultdict(list)
    for r in recs:
        if r["run_id"]:
            runs[r["run_id"]].append(r)
    if runs:
        print("\nPER RUN (calls tagged with a run_id)")
        for rid, rs in sorted(runs.items()):
            s = summarise(rs)
            print(f"  {rid}: {s['calls']} calls, {s['failed']} failed, ${s['cost']:.4f}, prompt {rs[0]['prompt_version']}")


if __name__ == "__main__":
    main()
