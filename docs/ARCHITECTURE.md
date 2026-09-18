# Architecture

Status: living document, updated every sprint (see [BACKLOG.md](BACKLOG.md)).

## Data pipeline (medallion)

```
BRONZE                          data/bronze/manifest.json
  Raw JSONL referenced in place (NOT mirrored into Parquet — see decision below).
    |
    v
SILVER HOSTS                    data/silver/hosts.parquet
  Flattened, typed, one row per (ip, port, timestamp) observation.
  Nested structs (http, ssl, vulns, location, _shodan, tags) exploded into
  queryable columns. 8,914,693 rows (368.7MB Parquet, down from 84.6GB raw).
    |
    v
ENTITY RESOLUTION               pipeline/entity_resolution/
  host row -> company_domain. Heuristics: hostnames/domains field first,
  then SSL cert subject org, then org/isp filtered against a hyperscaler/
  CDN denylist (Google, Amazon, Cloudflare, Alibaba, Incapsula, Akamai, ...).
  Unresolved rows go to an explicit "unresolved" bucket, not discarded silently.
    |
    v
SILVER COMPANIES                data/silver/companies.parquet
  One row per resolved company (domain, name, country, host_count, asn_type).
    |
    +----------------------+----------------------+
    v                      v
SECURITY SIGNALS      COMPANY METADATA
(urgency inputs)       (fit inputs)
  - CVE count/severity    - host/service count (size proxy)
  - EOL product count     - non-hyperscaler ASN flag
  - self-signed certs     - inferred vertical (product/tag mix)
  - risky open ports      - geography
  - honeypot/c2 flags     - reachability (has domain)
    +----------------------+----------------------+
                       v
                     GOLD                          data/gold/accounts.parquet
       company, fit_score, urgency_score, contact_score, band, why[]
                       |
          +------------+------------+
          v                         v
   score >= 85 -> auto-flag   50-84 -> LLM adjudication (skills/account-scoring)
          |                         |
          +------------+------------+
                       v
      Hunter.io enrichment (contact_flag=true accounts only)
                       |
                       v
              API (FastAPI) + chatbot (tool-use over Gold + contact API)
```

## Rule vs. LLM split (and why)

| Decision | Rules | LLM | Why |
|---|---|---|---|
| Fit / urgency scoring | Yes | No | Deterministic, auditable, cheap at 8.9M-row scale — a salesperson must be able to see exactly which rows produced the number. |
| Score >=85 / <50 routing | Yes | No | Clear-cut; an LLM call here would just be an expensive way to read a number off a threshold. |
| 50-84 band adjudication | No | Yes | This is a genuine judgment call — do the specific combination of signals justify outreach even though the aggregate score is borderline. Only the band where judgment changes the outcome gets an LLM call. |
| Company exposure summary (sales-readable) | No | Yes | Turning 10-30 raw signal rows into a 3-sentence brief a rep can use in an email is a language task, not a scoring task. |
| Outreach draft | No | Yes | Same reason. |
| Chatbot answer synthesis | No (tool-calling for retrieval) | Yes (for the response) | Retrieval against Gold/contact API is a function call; explaining the result in natural language is the LLM's job. |

## Model choice per task

- **Borderline adjudication + summary + outreach draft:** a stronger model
  (judgment-heavy, low volume — only the 50-84 band, a small slice of accounts).
- **Any bulk classification (e.g. banner/product normalization at scale):**
  cheapest model available, since it's high-volume and low-judgment.
- See [docs/COST_MODEL.md](COST_MODEL.md) for the actual token/cost math once
  Sprint 4 numbers exist.

## Key trade-offs

- **Bronze is the raw JSONL file in place, not a Parquet mirror.** The source
  schema has 60+ top-level struct columns, many device-specific (`hikvision`,
  `mongodb`, `mikrotik_winbox`, `philips_hue`, ...) that are irrelevant to
  scoring. Unifying all of them into one Parquet schema would cost a full
  84.6GB rewrite (disk budget doesn't have headroom for a duplicate at that
  size) for zero downstream benefit. Silver Hosts is produced directly from
  the JSONL via `pipeline/silver/hosts.py`, using an explicit DuckDB column
  projection covering only the fields entity resolution and scoring need.
  `data/bronze/manifest.json` records the source file's identity
  (size/mtime/row-count estimate) so the pipeline is still reproducible and
  auditable without the duplicate copy.
- **SSL certificate subject/issuer org is not parsed.** The `ssl` field in
  this dataset only carries the raw PEM chain, `chain_sha256`, and `jarm` —
  not a pre-parsed subject/issuer. Decoding x509 to pull an org name would
  require a certificate parser per record and only covers the ~11% of rows
  that have `ssl` at all. Deferred: entity resolution relies on
  `hostnames`/`domains` (73.6% coverage) as the primary signal instead. The
  `self-signed` tag (already provided) covers the security-hygiene signal
  this would have added, so no scoring value is lost.
- **EPSS, not just CVSS, drives urgency.** Each CVE in `vulns` carries an
  `epss` score (probability of exploitation in the wild) and `ranking_epss`
  (percentile) alongside CVSS severity. A high-CVSS, low-EPSS CVE is a
  theoretical risk; a mid-CVSS, high-EPSS CVE is being actively exploited
  right now — the latter is the more defensible "why now" for outreach, so
  urgency scoring weights EPSS explicitly rather than treating CVSS as the
  whole signal (see [BACKLOG.md](BACKLOG.md) Sprint 3).
- **Entity resolution is heuristic, not ground truth.** We don't have a
  canonical company registry. Precision is favored over recall — better to
  under-resolve (leave a host unresolved) than to merge two unrelated
  companies under one domain guess.
- **Contact enrichment quota.** One-time batch run against Hunter.io's free
  tier (25 domain searches/month), called only for `contact_flag=true`
  accounts post-Gold. No mock-fallback layer, per product decision — this is
  a one-off demo run, not a recurring production job.
- **Chatbot is tool-use, not RAG.** Gold is structured; there's no unstructured
  document corpus worth embedding. The LLM calls functions (query Gold, fetch
  contact) and narrates the result.

## Tech stack

- **Pipeline:** DuckDB + Parquet (handles the 84.6GB JSONL source without
  loading it into memory; columnar output is fast to query at Gold scale).
- **Scoring:** Python, pure rules (pandas/duckdb SQL), no LLM.
- **LLM:** Claude API, via versioned prompts (see [prompts/](../prompts)).
- **Enrichment:** Hunter.io REST API.
- **API:** FastAPI serving the Gold table + contact lookups.
- **Chatbot:** thin tool-calling loop over the same API.
- **Evals/tracing:** see [evals/](../evals) and the logging schema below.

## Tracing schema (every LLM call)

JSONL, one line per call, in `evals/results/traces.jsonl`:

```json
{"ts": "...", "call_id": "...", "skill": "account-scoring", "prompt_version": "v1",
 "model": "...", "input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
 "cost_usd": 0.0, "request": {...}, "response": {...}, "decision": "contact|skip|unsure"}
```
