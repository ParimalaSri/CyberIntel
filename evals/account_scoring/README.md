# Account Scoring — Eval Set

`labeled_set.jsonl`: 28 hand-labeled examples for the account-scoring
adjudication task (Sprint 4). Each line is one company from the Gold layer's
`review` band (contact_score 50-84 — the range where a rule-based threshold
alone isn't a confident enough decision).

## What's being measured

Given an account's fit/urgency scores and signals, the LLM's job is to
adjudicate one of three labels:

- **contact** — corroborating evidence justifies outreach despite the
  borderline score
- **skip** — evidence is too thin (e.g. a CVE count alone with nothing else)
- **escalate** — genuinely ambiguous; a human should look before either
  auto-decision (e.g. strong evidence but an unclear/tiny company, or a name
  that risks being another ISP-infrastructure false positive)

## Sampling

Stratified across the full 50-84 score range (7 buckets of 5 points each,
~4 examples per bucket, randomly sampled within each), not just the
top-scoring end — the eval needs to test judgment across the whole band,
not just the easy cases near 84.

## Labeling methodology — read before trusting the precision/recall number

**There is no objective ground truth here.** No one has actually contacted
these 187k companies to know whether outreach would have converted. These
labels are expert judgment (applying a stated rule — see below — to real
signals), not a hidden true answer. Precision/recall against this set
measures *agreement with that judgment*, not "correctness" in an absolute
sense. This is disclosed deliberately, not glossed over — see PLANNING.md's
"Success criteria."

**Rule applied:** corroboration matters more than the score itself. An
account with only a CVE count and nothing else (`why` has one or two
generic entries) is a thin, automated CPE-based match — labeled `skip`.
2+ signal types (CVE + EOL, + self-signed, + risky port, + screenshot
evidence) — labeled `contact`, even at a lower score, since breadth of
evidence is more trustworthy than any single automated signal. Strong
evidence paired with an unclear/tiny company profile (very low `fit_score`),
or a company name resembling a known ISP/telecom (risking the same
`comcast.net`-style false positive already found and fixed for domain-tier
resolution) — labeled `escalate`, not auto-decided either way.

**Process:** candidates were pulled live from `data/gold/accounts.parquet`,
labels were drafted by applying the rule above, presented for review as an
artifact table, and approved as-is.

## Distribution

16 contact / 8 skip / 4 escalate.
