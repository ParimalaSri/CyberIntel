"""Compare two prompt versions of a skill on the labeled eval set, offline.

Usage:
  python evals/compare_prompts.py v1 v2
  python evals/compare_prompts.py v1 v2 --traces path/to/traces.jsonl

No model calls: it reads the predictions that `run_eval.py --prompt-version X`
already logged to evals/results/traces.jsonl, so comparing is free and
repeatable. For each version it uses the latest *successful* trace per labeled
example (failed/unparseable calls are ignored), then compares only the
examples both versions have a prediction for.

Reports: accuracy and per-label precision/recall with deltas, the examples
that flipped (and whether the new version fixed or broke them), cost and
latency per version, and prompt-hash warnings (a version name whose prompt
text changed after runs were recorded is not a stable comparison).
"""
import argparse
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from llm import prompts  # noqa: E402
from llm.tracing import TRACE_PATH  # noqa: E402
from run_eval import LABELS, load_examples, score  # noqa: E402

SKILL = "account-scoring"


def latest_predictions(traces, version, examples):
    """{company_key: trace} - latest ok trace per labeled example for a version,
    matched on the exact labeled input so unrelated calls are never picked up."""
    by_key = {e["company_key"]: e for e in examples}
    chosen, hashes = {}, set()
    for t in traces:
        if t["prompt_version"] != version or t["status"] != "ok" or not t["decision"]:
            continue
        key = (t["request"] or {}).get("company_key")
        ex = by_key.get(key)
        if ex is None or (t["request"] or {}).get("input") != ex["input"]:
            continue
        hashes.add(t["prompt_hash"])
        if key not in chosen or t["ts"] >= chosen[key]["ts"]:
            chosen[key] = t
    return chosen, hashes


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def delta(a, b):
    return "n/a" if a is None or b is None else f"{(b - a) * 100:+.0f}pt"


def p50(vals):
    s = sorted(vals)
    return s[len(s) // 2] if s else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version_a")
    ap.add_argument("version_b")
    ap.add_argument("--traces", default=TRACE_PATH)
    args = ap.parse_args()
    a, b = args.version_a, args.version_b

    import llm.tracing as tracing
    tracing.TRACE_PATH = args.traces
    traces = tracing.read_traces(SKILL)
    examples = load_examples()
    by_key = {e["company_key"]: e for e in examples}

    runs, hashes = {}, {}
    for v in (a, b):
        runs[v], hashes[v] = latest_predictions(traces, v, examples)
        if not runs[v]:
            print(f"No successful traces for prompt {v} on the labeled set. Run:\n"
                  f"  python evals/run_eval.py --prompt-version {v}")
            return

    print(f"Prompt comparison: {a} vs {b}  ({len(examples)} labeled examples)")
    for v in (a, b):
        try:
            cur = prompts.load(SKILL, v).hash
        except FileNotFoundError:
            cur = None
        seen = sorted(h or "unknown" for h in hashes[v])
        note = ""
        if len([h for h in hashes[v] if h]) > 1:
            note = "  <-- WARNING: several prompt texts recorded under this version name"
        elif cur is None:
            note = "  <-- note: no prompt file for this version on disk"
        elif any(h and h != cur for h in hashes[v]):
            note = "  <-- WARNING: prompt file changed since these runs"
        print(f"  {v}: predictions for {len(runs[v])}/{len(examples)} examples | "
              f"hash in traces {seen} | hash on disk {cur or 'n/a'}{note}")

    paired = sorted(set(runs[a]) & set(runs[b]), key=lambda k: by_key[k]["id"])
    if not paired:
        print("\nNo examples have a successful prediction under both versions.")
        return
    exp = [by_key[k]["expected_label"] for k in paired]
    pa = [runs[a][k]["decision"] for k in paired]
    pb = [runs[b][k]["decision"] for k in paired]
    ra, rb = score(pa, exp), score(pb, exp)

    print(f"\nCompared on {len(paired)} examples with a prediction under both versions")
    print(f"\n{'':22s}{a:>8s}{b:>8s}{'change':>9s}")
    print(f"{'accuracy':22s}{pct(ra['overall_accuracy']):>8s}{pct(rb['overall_accuracy']):>8s}"
          f"{delta(ra['overall_accuracy'], rb['overall_accuracy']):>9s}")
    for lab in LABELS:
        for m in ("precision", "recall"):
            print(f"{lab + ' ' + m:22s}{pct(ra[lab][m]):>8s}{pct(rb[lab][m]):>8s}"
                  f"{delta(ra[lab][m], rb[lab][m]):>9s}")

    flips = [(k, x, y) for k, x, y in zip(paired, pa, pb) if x != y]
    print(f"\nFlipped examples: {len(flips)} of {len(paired)}")
    for k, x, y in flips:
        e = by_key[k]["expected_label"]
        verdict = f"{b} FIXES it" if y == e else (f"{b} BREAKS it" if x == e else "both wrong")
        print(f"  {by_key[k]['id']} {k[:30]:30s} expected={e:9s} {a}={x:9s} {b}={y:9s} -> {verdict}")
    fixed = sum(1 for k, x, y in flips if y == by_key[k]["expected_label"])
    broke = sum(1 for k, x, y in flips if x == by_key[k]["expected_label"])
    print(f"  net: {b} fixes {fixed}, breaks {broke}")

    print("\nCost / latency (successful traces used above, estimated list price)")
    for v in (a, b):
        ts = [runs[v][k] for k in paired]
        n = len(ts)
        print(f"  {v}: avg {sum(t['input_tokens'] for t in ts) / n:.0f} in / "
              f"{sum(t['output_tokens'] for t in ts) / n:.0f} out tokens, "
              f"p50 latency {p50([t['latency_ms'] for t in ts]) / 1000:.1f}s, "
              f"est ${sum(t['cost_usd'] for t in ts) / n * 1000:.2f} per 1,000 calls")

    print("\nThe labeled set was hand-labelled with the v1 rule; this measures agreement with it, "
          "not real-world conversion.")


if __name__ == "__main__":
    main()
