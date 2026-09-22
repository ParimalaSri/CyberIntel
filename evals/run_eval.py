"""One-command eval harness for the account-scoring skill.

Usage: python evals/run_eval.py [--prompt-version v1]

Requires GROQ_API_KEY in the environment (or in a .env file at the repo
root). Calls the model once per labeled example, logs every call via
llm.tracing, compares predicted vs expected label, and reports
precision/recall/F1 per label plus overall accuracy - versus the previous
run's results if one exists.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
from llm.tracing import log_trace  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

LABELED_SET = os.path.join(os.path.dirname(__file__), "account_scoring", "labeled_set.jsonl")
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts", "account_scoring")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
MODEL = "openai/gpt-oss-120b"  # via Groq (free tier, no card required)
LABELS = ["contact", "skip", "escalate"]


def load_prompt(version: str) -> str:
    path = os.path.join(PROMPT_DIR, f"{version}.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_examples() -> list[dict]:
    examples = []
    with open(LABELED_SET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def call_model(client, prompt_template: str, account_input: dict) -> tuple[dict, dict, float]:
    import groq

    filled = prompt_template.replace("{{account_json}}", json.dumps(account_input))
    t0 = time.time()
    for attempt in range(5):
        try:
            msg = client.chat.completions.create(
                model=MODEL,
                max_tokens=1536,  # gpt-oss reasoning models spend most of the
                messages=[{"role": "user", "content": filled}],  # budget thinking before the final JSON
            )
            break
        except groq.RateLimitError:
            wait = 8 * (attempt + 1)  # free-tier TPM limit - back off and retry
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
    else:
        raise RuntimeError("rate limited after 5 retries")
    latency_ms = (time.time() - t0) * 1000
    text = msg.choices[0].message.content.strip()
    # strip a ```json fence if the model added one despite instructions
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"label": "escalate", "rationale": f"unparseable response: {text[:200]}", "confidence": "low"}
    usage = {"input_tokens": msg.usage.prompt_tokens, "output_tokens": msg.usage.completion_tokens}
    return parsed, usage, latency_ms


def score(predictions: list[str], expected: list[str]) -> dict:
    report = {}
    for label in LABELS:
        tp = sum(1 for p, e in zip(predictions, expected) if p == label and e == label)
        fp = sum(1 for p, e in zip(predictions, expected) if p == label and e != label)
        fn = sum(1 for p, e in zip(predictions, expected) if p != label and e == label)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
        report[label] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
    accuracy = sum(1 for p, e in zip(predictions, expected) if p == e) / len(expected)
    report["overall_accuracy"] = accuracy
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default="v1")
    args = ap.parse_args()

    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set. Set it in your environment and re-run.", file=sys.stderr)
        sys.exit(1)

    from groq import Groq
    client = Groq()

    prompt_template = load_prompt(args.prompt_version)
    examples = load_examples()
    print(f"Running {len(examples)} examples against prompt {args.prompt_version} ({MODEL})...")

    predictions, expected, total_cost = [], [], 0.0
    for ex in examples:
        parsed, usage, latency_ms = call_model(client, prompt_template, ex["input"])
        label = parsed.get("label", "escalate")
        predictions.append(label)
        expected.append(ex["expected_label"])
        rec = log_trace(
            skill="account-scoring",
            prompt_version=args.prompt_version,
            model=MODEL,
            request={"company_key": ex["company_key"], "input": ex["input"]},
            response=parsed,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
            decision=label,
        )
        total_cost += rec["cost_usd"]
        match = "OK" if label == ex["expected_label"] else "MISS"
        print(f"  [{match}] {ex['company_key']:35s} expected={ex['expected_label']:9s} got={label}")

    report = score(predictions, expected)
    report["prompt_version"] = args.prompt_version
    report["model"] = MODEL
    report["n_examples"] = len(examples)
    report["total_cost_usd"] = round(total_cost, 4)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"eval_report_{args.prompt_version}.json")
    prev = None
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            prev = json.load(f)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== Results ({args.prompt_version}) ===")
    print(f"Overall accuracy: {report['overall_accuracy']:.1%}")
    for label in LABELS:
        s = report[label]
        p = f"{s['precision']:.1%}" if s["precision"] is not None else "n/a"
        r = f"{s['recall']:.1%}" if s["recall"] is not None else "n/a"
        print(f"  {label:9s} precision={p:>6s} recall={r:>6s} (tp={s['tp']} fp={s['fp']} fn={s['fn']})")
    print(f"Total cost: ${report['total_cost_usd']}")
    if prev:
        delta = report["overall_accuracy"] - prev["overall_accuracy"]
        print(f"\nvs. previous {args.prompt_version} run: accuracy {delta:+.1%}")


if __name__ == "__main__":
    main()
