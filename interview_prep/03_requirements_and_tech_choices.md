# CyberIntel: requirements and the technologies actually used (and why not the alternatives)

Only technologies that appear in the repo's code or config are listed. Claude/Anthropic is NOT used in any code path (it appears only in docs and in the price table in `llm/tracing.py`), so don't say you used it.

Source tags: **[DOC]** the repo documents this reasoning. **[CONSTRAINT]** forced by a real limit (disk, free tier, signup). **[MY INFERENCE]** a sound reason, but the repo doesn't record it, so say it as "the reasoning was..." and don't pretend you wrote it down at the time.

---

## 1. Requirements

### Functional (from PLANNING.md)
| # | Requirement | Where it's met |
|---|---|---|
| F1 | Turn IP/port-level scan rows into **company-level** accounts | `resolve.py` |
| F2 | Rank accounts by **fit x urgency** | `scoring.py` |
| F3 | Give a **reason** for every score ("why now") | `why[]` in `scoring.py` |
| F4 | Decide borderline accounts with judgment | `adjudicate.py`, prompt v1 (run on 5 rows only) |
| F5 | Provide a **contact** for flagged accounts | `snov_enrich.py` (25 calls, 17 found) |
| F6 | Let a rep **ask in plain English** | `chatbot.py`, `server.py`, UI |
| F7 | Don't force-match everything: keep an explicit **unresolved** bucket | `resolve.py` |

### Non-functional
| # | Requirement | Evidence |
|---|---|---|
| N1 | Handle **84.6GB** on one machine without loading it into memory | DuckDB out-of-core, 4GB cap |
| N2 | **Auditability**: every score traceable to rows | `host_company_map.parquet`, `why[]` |
| N3 | **Precision over recall** in entity resolution | ARCHITECTURE.md, denylists |
| N4 | **Cost ceiling**: $0 infra, $50/run LLM budget | COST_MODEL.md, free tiers |
| N5 | **Measured LLM behavior**, not a black box | `evals/`, `llm/tracing.py` |
| N6 | **Reproducible** without storing a duplicate copy | `manifest.py`, scripts run in order |

Not requirements (don't claim): SLAs, freshness targets, multi-tenancy, concurrent users, recurring runs. None are specified anywhere.

---

## 2. Technologies used: why this one, why not the others

### Data format and storage

**JSONL (input)** [CONSTRAINT: the provider's format]
- Not a choice. One JSON object per line.
- Hard because: row-oriented text, no schema, nothing can be skipped (no pushdown), every byte must be parsed. One record was 25MB and broke the default parser limit.

**zstd (compression of the source, and Parquet codec)** [CONSTRAINT for the source; MY INFERENCE for Parquet]
- Source arrived as `.zst`. For Parquet output, ZSTD gives a better ratio than Snappy at a modest CPU cost, and it suits write-once, read-many tables.
- Why not Snappy: bigger files, faster decode. Why not gzip: slower to decompress, no better ratio than zstd.

**Parquet (Silver and Gold)** [DOC: "columnar output is fast to query"]
- Columnar: queries read only needed columns (projection). Row-group min/max stats allow skipping. Typed schema. Compressed. 84.6GB JSON → 386MB.
- Why not CSV: no types, no nested data (your `domains`, `vulns` are lists and maps), no pushdown. Why not keep JSON: reparsing every query. Why not a database table: no server needed and the files are portable.
- Cost of the choice: no updates in place (full rewrite per stage), no atomic multi-file commits, and Parquet has no 128-bit int (the HUGEINT→DOUBLE bug).

**Local disk** [CONSTRAINT: single laptop]
- Why not S3: no cloud account or budget (the AWS target is documented, not deployed, N4).

### Processing

**DuckDB** [DOC: "single-node, out-of-core... not a Spark-cluster-scale problem"]
- Embedded analytical SQL engine. Reads JSON and Parquet directly, runs vectorized, multithreaded, and spills to disk past the memory limit. No cluster to run.
- Fits because the job is a one-time 85GB transform with joins and aggregations, on one machine.
- **Why not Spark:** needs a cluster or at least a JVM setup, adds shuffle and serialization overhead, and gives no benefit when the data fits one node's disk. The real argument is operational cost versus benefit at this size.
- **Why not pandas:** loads data into memory; 84.6GB won't fit. (It's in `requirements.txt` but the pipeline doesn't use it; only exploratory `.df()` calls need it.)
- **Why not a warehouse (Snowflake/BigQuery):** cost, account setup, and upload time for 85GB for a one-time job.
- **Why not Postgres:** row store, slow for scans and aggregations at this size, and needs loading.
- **Limits:** one node, no HA, no distributed shuffle. At the scale in the JD (billions of records) it stops being the right tool, and you should say when (working set larger than a single node, recovery time target, many daily snapshots).

**SQL** (the transformation language) [MY INFERENCE]
- Joins, group-bys, window-free aggregation, list/map functions. Declarative, auditable, runs in the engine. Why not Python loops or pandas apply: far slower, memory-bound.

**Python** (glue) [MY INFERENCE]
- Runs each SQL script, handles files, calls APIs. Why not shell/Make: no real logic or API clients. Not an orchestrator (nothing schedules or retries).

**zstandard (Python lib)** [CONSTRAINT]
- Streams the 12.4GB `.zst` to disk in constant memory. (Missing from `requirements.txt`.)

### LLM layer

**Groq, `openai/gpt-oss-120b` (adjudication, eval)** [CONSTRAINT: free tier, no card]
- The repo says why: "free tier, no card required". Strong open-weights reasoning model on fast inference.
- Why not Claude/GPT: cost/credit constraint during development (docs and price table were written assuming Claude; the code uses Groq). Say honestly: "I used what was free; the prompt and eval harness are provider-agnostic, so I could compare models."
- Why a larger model: low-volume judgment (docs: stronger model for judgment, cheap for retrieval).
- Costs of the choice: reasoning model → large token use and ~12.8s latency, needed `max_tokens` 1536 or the JSON truncates.

**Groq, `openai/gpt-oss-20b` (chatbot)** [DOC]
- Small model for lookup/tool-calling: cheaper and faster than the judgment model.
- Why not RAG: Gold is structured; there's no document corpus. The model calls functions and narrates the result.

**Versioned prompts in git + JSONL tracing + eval script** [DOC]
- Why not a framework (LangChain, Langfuse): small scope, zero setup, full visibility. Trade-off: no concurrency-safe tracing, no UI, no run IDs.

### Enrichment

**Snov.io** [CONSTRAINT: Hunter.io needed a work email to sign up]
- Domain search → emails and titles, free-tier quota, hence only 25 top accounts.
- Why not Hunter: signup blocked. Why not scraping or buying a contact list: legal/cost, out of scope. The code comment admits the Snov endpoint shape came from general knowledge and wasn't verified against live docs at write time.
- Why a **gated** call (contact band, domain tier only): the quota is scarce, and org-tier accounts have no domain to look up. Don't spend on noise.

### Serving

**FastAPI + uvicorn + pydantic** [MY INFERENCE]
- Thin HTTP layer: typed request model, auto-docs, simple. Why not Flask: less built-in validation. Why not a framework app (Django): overkill.

**Static HTML/JS UI** [DOC: "minimal static chat UI"]
- No build step. Why not React: not needed for one page.

**python-dotenv** [MY INFERENCE]
- Loads API keys from a gitignored `.env`. Why not hard-coding: leaks keys. (In production: a secrets manager.)

**requests** [MY INFERENCE]
- Simple HTTP for Snov. Why not an SDK: none used. No retry/backoff configured.

**Render (config only)** [CONSTRAINT: $0]
- `render.yaml` defines a free-tier web service. The backlog marks deployment as not done (Sprint 7 unchecked), so don't claim it's live unless you've deployed it.

### Process / documentation

**Git, sprint-style BACKLOG.md, Mermaid diagrams** [DOC]
- Not data technologies. Mention only briefly (history of 27 commits, issue log).

---

## 3. Technologies NOT used (don't claim them; know why you'd add them)

| Not used | Why it wasn't needed here | When it would be |
|---|---|---|
| Spark | 85GB fits one node | Working set or SLA beyond a node, billions of rows |
| Airflow | Manual runs, one-time batch | Recurring snapshots, retries, backfills, SLAs |
| dbt | SQL lives in Python scripts | Many models needing tests, docs, lineage |
| Iceberg / Delta | Single files, full refresh | Incremental merges, atomic commits, schema evolution |
| S3 / cloud | $0 constraint | Durable shared storage, multiple consumers |
| Kafka / streaming | Source is a batch snapshot | Real-time signals |
| Great Expectations / dbt tests | Manual QA | Automated quality gates |
| Fuzzy matching libraries | Precision over recall, rules sufficed | Alias/subsidiary resolution |
| Vector DB / RAG | Data is structured | Unstructured document search |
| LLM gateway / Langfuse / OpenTelemetry | JSONL file was enough | Multi-team, concurrent, cost dashboards |
| pytest / CI | No tests written | Any production code |

---

## 4. If Suresh asks "why DuckDB, honestly?"

"The dataset is 85GB and the job is a one-time batch with joins and group-bys, so a single-node columnar engine with out-of-core execution was the lowest-effort correct tool. Spark would have added cluster overhead with no speedup at this size. I'd change that decision when the daily volume or the join state no longer fits one machine, or when I need recovery time better than 'rerun the whole thing'."
