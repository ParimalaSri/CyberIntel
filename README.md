# CyberIntel — AI Sales Intelligence Platform

Turns a raw internet-wide scan export (Shodan-style, 84.6GB, ~8.9M records)
into a ranked, explained account list for a cybersecurity software sales
team: which companies actually need us right now, why, and who to contact.

**Status:** data pipeline + scoring complete and verified against real data
(Sprint 3.5). LLM adjudication skill/prompt/eval built (Sprint 4, in
progress). See [docs/BACKLOG.md](docs/BACKLOG.md) for exact sprint status.

- [Planning doc](docs/PLANNING.md) — the use cases chosen, and why
- [Architecture doc](docs/ARCHITECTURE.md) — design decisions, trade-offs, rule-vs-LLM split, tracing schema
- [Issues & Resolutions](docs/ISSUES_AND_RESOLUTIONS.md) — every data-quality bug found and fixed, with root cause ([presentation version](docs/issues-and-resolutions.html))
- [Backlog](docs/BACKLOG.md) — sprint-by-sprint task tracker

---

## High-level design

```mermaid
flowchart TD
    A["Raw Shodan JSONL\n84.6GB · 8.9M records"] --> B["Bronze\n(manifest only,\nsee trade-off below)"]
    B --> C["Silver Hosts\n8,914,693 rows"]
    C --> D["Entity Resolution\ndomain / org / unresolved"]
    D --> E["Silver Companies\n189,257 resolved"]
    E --> F["Security Signals\nCVE · EPSS · EOL · self-signed\nrisky ports · screenshot evidence"]
    F --> G["Gold Scoring\nfit_score x urgency_score"]
    G --> H1["contact\n155 accounts"]
    G --> H2["review\n13,159 accounts"]
    G --> H3["skip\n175,281 accounts"]
    G --> H4["exclude\n662 accounts"]
    H2 --> I["LLM Adjudication\n(account-scoring skill)"]
    H1 --> J["Contact Enrichment\nHunter.io — real calls,\ncontact band only"]
    I -->|approved| J
    J --> K["API"]
    K --> L["Chatbot"]
    K -.->|planned, not deployed| M["AWS: S3 + DynamoDB\n+ Lambda + Batch/Fargate\n$0 cost ceiling, by design"]

    style H1 fill:#e5f6ea,stroke:#157f3c,color:#14171f
    style H2 fill:#fdf1de,stroke:#b5750a,color:#14171f
    style H3 fill:#eceef2,stroke:#5b6478,color:#14171f
    style H4 fill:#fbe9e9,stroke:#b42323,color:#14171f
    style M fill:#ffffff,stroke:#8792a8,stroke-dasharray: 5 5,color:#14171f
```

Solid boxes are running against real data today. The dashed AWS box is a
documented, containerization-ready target we deliberately didn't deploy —
see [ARCHITECTURE.md's cost trade-offs](docs/ARCHITECTURE.md) for why a $0
ceiling was treated as a hard constraint, not an excuse.

## Rule vs. LLM — where each one is used

```mermaid
flowchart LR
    subgraph Rules["Rules (SQL/DuckDB) — deterministic, free, auditable"]
        R1["fit_score / urgency_score\ncomputation"]
        R2["contact / skip / exclude\nband routing (score outside 50-84)"]
        R3["Entity resolution\ndomain/org/unresolved"]
    end
    subgraph LLM["LLM — judgment tasks only, low volume"]
        L1["review band adjudication\n(score 50-84, corroboration\njudgment call)"]
        L2["Company exposure summary\nfor outreach (planned)"]
        L3["Outreach draft\n(planned)"]
    end
    Rules -->|"~99.9% of accounts\ndecided here"| Done1["Done"]
    LLM -->|"~7% of accounts,\nwhere a threshold\nisn't confident enough"| Done2["Done"]
```

Rules decide the clear cases because a salesperson must be able to see
exactly which rows produced a number — an LLM call there would just be an
expensive way to read a number off a threshold. The LLM is reserved for the
one band where the *combination* of signals, not the aggregate score,
determines the right call. Full reasoning in
[ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Entity resolution — how 8.9M host rows become 189K companies

```mermaid
flowchart TD
    A["8,914,693 host rows\n(ip, port, timestamp) grain"] --> B{"domains field\nsurvives denylist?"}
    B -->|yes| C["domain tier\n795,989 rows\n136,994 companies"]
    B -->|no| D{"org isn't\nhyperscaler/ISP?"}
    D -->|yes| E["org tier\n1,267,451 rows\n52,263 companies"]
    D -->|no| F["unresolved\n6,979,617 rows (78.3%)\ncounted, not merged"]

    C --> G["Silver Companies\n189,257 resolved"]
    E --> G

    style F fill:#eceef2,stroke:#5b6478,color:#14171f
    style G fill:#e5f6ea,stroke:#157f3c,color:#14171f
```

The 78.3% unresolved rate is expected, not a gap to close — it reflects
this dataset's real skew toward hyperscaler/CDN tenant IPs, where the
actual business behind an IP genuinely isn't observable in the scan data.
Precision was chosen over recall throughout; see
[Issues & Resolutions](docs/ISSUES_AND_RESOLUTIONS.md) for the dozen bugs
found while tightening this.

## Scoring model

```mermaid
flowchart LR
    subgraph Fit["fit_score (0-100)"]
        F1["+40 reachable\n(has a domain)"]
        F2["+20 size\n(host_count, capped)"]
        F3["+20 vertical\n(exposed database)"]
        F4["+20 geography\n(target market)"]
    end
    subgraph Urgency["urgency_score (0-100)"]
        U1["+25 CVE x EPSS"]
        U2["+10 recent CVE"]
        U3["+10 verified CVE"]
        U4["+15 EOL software"]
        U5["+10 self-signed cert"]
        U6["+20 risky open port"]
        U7["c2 or screenshot\nevidence -> override to 100"]
    end
    Fit --> S["contact_score =\n0.5 x fit + 0.5 x urgency"]
    Urgency --> S
    S --> B1["≥85 → contact"]
    S --> B2["50-84 → review"]
    S --> B3["<50 → skip"]
    S -.-> B4["honeypot or\nlikely_infra_or_proxy\n→ exclude (overrides band)"]
```

## Data schema by layer

### Silver Hosts (`data/silver/hosts.parquet`) — 8,914,693 rows, 43.4 bytes/row

One row per exposed service observation. Flattened from the raw JSONL's 60+
top-level fields down to what entity resolution and scoring actually need.

| Column | Type | Notes |
|---|---|---|
| `ip_str`, `port`, `transport`, `ts` | varchar/bigint/timestamp | identity |
| `hostnames`, `domains` | varchar[] | entity resolution backbone |
| `country_code`, `country_name`, `city`, `lat/long` | varchar/double | from `location` |
| `org`, `isp`, `asn` | varchar | ownership (noisy — see denylist trade-offs) |
| `os`, `product`, `devicetype`, `tags`, `cpe23` | varchar/varchar[] | fingerprint |
| `vulns` | `MAP<cve_id, {cvss, epss, verified, ...}>` | CVE exposure |
| `ssl_jarm`, `http_status/server/title` | varchar/bigint | protocol signals |
| `screenshot_labels`, `screenshot_text` | varchar[]/varchar | Shodan-precomputed OCR (rare, ~0.03%, very high signal) |
| `is_eol_product`, `is_self_signed`, `is_honeypot`, `is_c2`, `is_iot`, `is_vpn`, `is_database_tag` | boolean | derived tag flags |

### Silver Companies (`data/silver/companies.parquet`) — 189,257 rows

One row per resolved `(company_key, resolution_tier)`. Key columns:
`company_key`, `resolution_tier` (`domain`/`org`), `host_count`,
`country_count`, `primary_country_code`, `asn_count`, `primary_org`,
`primary_isp`, `sample_ips`, `sample_hostnames`, `likely_infra_or_proxy`.

### Security Signals (`data/gold/security_signals.parquet`) — 189,257 rows

Per-company urgency inputs: `cve_count`, `verified_cve_count`, `max_cvss`,
`max_epss`, `has_recent_cve`, `eol_count`, `self_signed_count`,
`honeypot_count`, `c2_count`, `risky_open_port_count`,
`distinct_port_count`, `database_tag_count`, `has_screenshot_evidence`,
`screenshot_evidence_text`.

### Gold Accounts (`data/gold/accounts.parquet`) — 189,257 rows

Everything above, joined, plus `fit_score`, `urgency_score`,
`contact_score`, `band` (`contact`/`review`/`skip`/`exclude`), and `why[]`
— a human-readable explanation array a salesperson reads directly.

## Repository structure

```
CyberIntel/
├── pipeline/
│   ├── bronze/              # source-file manifest (not a data copy — see ARCHITECTURE.md)
│   ├── silver/               # JSONL -> Silver Hosts (DuckDB)
│   ├── entity_resolution/    # host rows -> companies (denylists + tiering)
│   └── gold/                 # Security Signals + Scoring
├── skills/
│   └── account-scoring/      # SKILL.md — reusable LLM workflow spec
├── prompts/
│   └── account_scoring/      # versioned prompts (v1.md, ...)
├── evals/
│   └── account_scoring/      # labeled_set.jsonl, results, run_eval.py
├── llm/                      # tracing.py — LLM call logging
├── api/                      # (Sprint 5)
├── app/                      # chatbot (Sprint 6)
├── docs/                     # PLANNING, ARCHITECTURE, BACKLOG, ISSUES + HTML companions
└── requirements.txt
```

## Running it locally

```bash
pip install -r requirements.txt

# 1. Decompress the source export (see docs/DATA_SOURCE.md for where to get it)
python scripts/decompress_source.py

# 2. Run the pipeline, in order
python pipeline/bronze/manifest.py
python pipeline/silver/hosts.py             # scans the full 84.6GB, ~90s on NVMe
python pipeline/entity_resolution/resolve.py
python pipeline/gold/security_signals.py
python pipeline/gold/scoring.py

# Sanity check any layer
python pipeline/silver/sanity_check.py
```

Output lands in `data/{bronze,silver,gold}/` (gitignored — regenerated by
the scripts above, not source-controlled).

## Tech stack & why

DuckDB + Parquet for the pipeline (single-node, out-of-core — the right
tool for an 84.6GB one-time transform, not a Spark-cluster-scale problem;
see [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full reasoning),
Claude for the judgment-only LLM tasks, Hunter.io for contact enrichment,
FastAPI for the API layer. AWS target (S3 + DynamoDB + Lambda) documented
and container-ready but not deployed, by deliberate cost-ceiling choice.
