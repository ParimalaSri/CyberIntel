---
name: account-scoring
description: Adjudicates borderline ("review" band) cybersecurity sales-lead accounts - decides contact / skip / escalate when the rule-based score alone isn't a confident-enough decision.
version: 1
---

# Account Scoring (Adjudication)

## Why this exists

The Gold-layer pipeline (`pipeline/gold/scoring.py`) computes a deterministic
`fit_score` x `urgency_score` -> `contact_score` for every resolved company,
and bands the result: `contact` (>=85), `skip` (<50), `exclude`
(honeypot/infrastructure noise), and `review` (50-84). The first three bands
are rule-decided on purpose - fast, free, auditable, and there's nothing an
LLM adds by re-deriving a number a SQL query already computed correctly.
`review` is different: it's the band where the aggregate score doesn't tell
you whether the *specific combination* of signals justifies outreach. That's
a judgment call, and this skill is that judgment call, made consistently and
loggable.

See `docs/ARCHITECTURE.md`'s "Rule vs. LLM split" for the fuller reasoning.

## Trigger conditions

Invoke this skill for any Gold-layer account where `band == "review"`
(`50 <= contact_score <= 84`). Do **not** invoke it for `contact`, `skip`,
or `exclude` bands — those are already decided, and running an LLM call
against a rule-decided row is pure wasted cost with no judgment to add.

## Inputs

One JSON object per account, read from `data/gold/accounts.parquet`:

```json
{
  "company_key": "string - the resolved company identifier",
  "resolution_tier": "domain | org",
  "primary_country_code": "string",
  "host_count": "int",
  "fit_score": "float 0-100",
  "urgency_score": "float 0-100",
  "contact_score": "float 0-100",
  "cve_count": "int",
  "max_cvss": "float or null",
  "max_epss": "float or null - exploitation-probability, 0-1",
  "eol_count": "int - hosts running end-of-life software",
  "self_signed_count": "int",
  "risky_open_port_count": "int - VNC/RDP/MSSQL/Telnet/FTP/MongoDB/Redis/Elasticsearch exposed",
  "c2_count": "int - hosts flagged for possible command-and-control activity",
  "has_screenshot_evidence": "bool - Shodan OCR'd text off an exposed RDP/VNC screenshot",
  "why": "string[] - the rule-generated explanation array, already human-readable"
}
```

## Outputs

```json
{
  "company_key": "echoed from input",
  "label": "contact | skip | escalate",
  "rationale": "1-2 sentences, specific to the signals present",
  "confidence": "high | medium | low"
}
```

- **contact** — override the borderline score, treat as if it had scored
  >=85. Proceeds to Sprint 5 contact enrichment.
- **skip** — confirm the borderline score doesn't justify outreach.
- **escalate** — neither rules nor the LLM should auto-decide this one; a
  human reviews it before it goes anywhere. Reserved for cases where the
  evidence is strong but something else is genuinely unclear (see the
  worked examples below) — not a dumping ground for every hard case.

## Dependent prompts

`prompts/account_scoring/v1.md` — current version. Compare future versions
against it with `evals/run_eval.py` (Sprint 4, in progress) before
promoting a new version; both the prompt file and the eval results are
versioned so a v1-vs-v2 regression is visible in git history, not just
asserted.

## Model

A single account-scoring call is a low-token, judgment-heavy, low-volume
task (13,159 review-band accounts total, not millions) — this is exactly
where the take-home's cost-model guidance says to spend on a stronger
model, not the cheapest one. See `docs/COST_MODEL.md` for the actual
token/cost math once that's written.

## Tracing

Every call must be logged per the schema in `docs/ARCHITECTURE.md`
("Tracing schema"): request, response, model, prompt version, latency,
cost, and the resulting `decision`. The `label` this skill returns is
exactly the `decision` field in that log.

## Worked example invocation

**Input** (`csd.co`, contact_score 50 — near the bottom of the band):

```json
{
  "company_key": "csd.co", "resolution_tier": "domain",
  "primary_country_code": "GB", "host_count": 1,
  "fit_score": 66.0, "urgency_score": 34.99, "contact_score": 50.0,
  "cve_count": 76, "max_cvss": 9.8, "max_epss": 0.99957,
  "eol_count": 0, "self_signed_count": 0, "risky_open_port_count": 0,
  "c2_count": 0, "has_screenshot_evidence": false,
  "why": ["76 known CVE(s), max CVSS 9.8, max EPSS 1.0", "Includes a 2025+ (recently disclosed) CVE"]
}
```

**Expected output:**

```json
{
  "company_key": "csd.co",
  "label": "skip",
  "rationale": "CVE count is the only signal present, with no EOL, self-signed, risky-port, or screenshot corroboration. A CVE-only match is CPE-inferred, not confirmed - too thin to justify outreach at this score.",
  "confidence": "high"
}
```

**Contrast** (`welcome italia s.p.a`, contact_score 57 — similarly
borderline, opposite decision):

```json
{
  "company_key": "welcome italia s.p.a", "resolution_tier": "org",
  "primary_country_code": "IT", "host_count": 43,
  "fit_score": 35.0, "urgency_score": 79.99, "contact_score": 57.0,
  "cve_count": 435, "max_cvss": 10.0, "max_epss": 0.99999,
  "eol_count": 1, "self_signed_count": 5, "risky_open_port_count": 1,
  "c2_count": 0, "has_screenshot_evidence": false,
  "why": ["435 known CVE(s), max CVSS 10.0, max EPSS 1.0", "Includes a 2025+ (recently disclosed) CVE", "1 host(s) running end-of-life software", "5 host(s) with self-signed TLS certificates", "1 host(s) with a high-risk port exposed (VNC/RDP/MSSQL/Telnet/FTP/MongoDB/Redis/Elasticsearch)"]
}
```

```json
{
  "company_key": "welcome italia s.p.a",
  "label": "contact",
  "rationale": "Four independent signal types corroborate each other (CVEs, EOL software, self-signed certs, a risky open port) - the low fit_score reflects a smaller company profile, not weak evidence. Breadth of evidence outweighs the raw score here.",
  "confidence": "medium"
}
```

Both examples score in the same narrow range (50-57); the label differs
entirely on corroboration, not the number. That's the judgment this skill
exists to make.

## Future extension (not yet built)

A second, related skill — classifying ambiguous **org-tier** company names
as "genuine business" vs. "unclassified infrastructure" our keyword
denylist missed (see `pipeline/entity_resolution/denylists.py`) — was
scoped during Sprint 3.5 but deferred; it's a different task (entity
classification, not score adjudication) and deserves its own eval set
rather than being bolted onto this one.
