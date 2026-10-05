# Prompts

Prompts are versioned files: `prompts/<skill_dir>/vN.md`
(skill `account-scoring` lives in `prompts/account_scoring/`).

- **A published version is immutable.** To change behaviour, add `vN+1.md`;
  never edit an existing version in place. Every trace records the prompt's
  content hash (`prompt_hash`), so an in-place edit shows up as two different
  hashes under one version name in `python evals/compare_prompts.py`.
- **Changelog header.** Each file starts with an HTML comment: what it is used
  by, what it was evaluated against, and what changed from the previous version.
- **Load through the registry**, not by path: `llm/prompts.py`
  (`prompts.load("account-scoring", "v2")`, `prompts.versions(...)`,
  `prompt.render(account_json=...)`).

## Comparing v1 and v2

1. Run the labeled set under each version (uses the model; Groq free tier):
   `python evals/run_eval.py --prompt-version v1` and `--prompt-version v2`.
2. Compare, with no further model calls (reads `evals/results/traces.jsonl`):
   `python evals/compare_prompts.py v1 v2`
   - accuracy and per-label precision/recall for each version, with deltas;
   - the examples whose prediction flipped, and whether v2 fixed or broke them;
   - tokens, latency and estimated cost per version.
3. Promote v2 (point `adjudicate.py --prompt-version v2`) only when it beats
   v1 on the labeled set and the flips are ones you can explain.

Caveat: the labeled set is hand-labelled with the same rule the v1 prompt
encodes (see `evals/account_scoring/README.md`), so it measures agreement with
that rule, not real-world conversion.
