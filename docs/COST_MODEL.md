# Cost Model

Scope: the account-scoring LLM call (`skills/account-scoring`), the only
LLM feature built so far. Figures below use `claude-sonnet-5` pricing in
`llm/tracing.py` — approximate, verify against Anthropic's pricing page.

## Per-call cost

- Prompt (`prompts/account_scoring/v1.md`, filled) ≈ 550-650 input tokens
  (rules + 3 few-shot examples + one account's JSON).
- Output ≈ 60-90 tokens (small JSON object).
- At $3/$15 per million input/output tokens: **~$0.0028/call**
  (600 in x $3/M + 75 out x $15/M ≈ $0.0018 + $0.0011).

## Volume

Only `review`-band accounts get a call — 13,159 out of 189,257 (7.0%).
Rule-decided bands (93%) cost nothing.

- **One full pass over the current review band:** 13,159 x $0.0028 ≈
  **$36.85**.
- **Per new scan snapshot** (if this ran on a recurring schedule against
  fresh Shodan exports): same order of magnitude per pass, assuming a
  similar review-band proportion.

## Model choice

Sonnet-class model for every call — the take-home's cost-model guidance
(cheap model for classification, stronger model for judgment) points here
because this *is* the judgment task: distinguishing "2+ corroborating
signals at a low score" from "one thin CVE-only signal at a similar score"
requires actual reasoning, not classification. There is no cheap-model
bulk-classification step in this pipeline yet — entity resolution and
scoring are pure SQL. If a future skill (e.g. org-tier "genuine business
vs. infrastructure" classification, noted as deferred in `SKILL.md`) is
added, that one *would* be a good Haiku-class candidate: high volume
(~1.2M org-tier rows), low judgment depth.

## Cost ceiling

**Proposed ceiling: $50/run.** A full review-band pass costs ~$37; this
gives headroom for the band growing somewhat (e.g. after Sprint 3.5-style
entity-resolution fixes shift more rows into `review`) without silently
blowing past budget. The eval harness (`evals/run_eval.py`) already reports
`total_cost_usd` per run, so a ceiling can be enforced by checking that
figure before proceeding to full-batch scoring, or by capping the batch
size directly.

## What's NOT counted here

Contact enrichment (Hunter.io) and any future outreach-drafting or
company-summary LLM calls are separate cost centers, not yet built —
they'll get their own entries here once they exist, not folded into this
one to inflate the appearance of coverage.
