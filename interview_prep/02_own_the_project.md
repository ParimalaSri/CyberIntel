# Own the CyberIntel project: Steps 1-5

Tags: [IMPLEMENTED] = exists in the repo and you must be able to defend it as yours (if an assistant wrote some of the code, you still own the decisions, so know why each choice was made). [DESIGNED] = designed or documented, not built. [PROD] = production improvement, not built, do not claim. [CONCEPT] = understand it, don't claim it.

Real numbers verified from the data on disk (use these, not the README's older ones):
- 8,914,693 observation rows, but only **7,907,271 distinct (ip, port)** and **3,087,232 distinct IPs**.
- 189,257 resolved companies. 155 contact (all domain-tier, 54 via c2/screenshot override), 13,159 review, 662 exclude, 175,281 skip.
- Silver hosts 386MB, companies 8MB, host_company_map 28MB (1,935,076 rows), accounts 9MB.

---

# STEP 1: One-page map

Order caveat: your list puts "LLM" before "scoring." In the code, **scoring runs first, then the LLM only sees the 'review' band**. Say it that way. Enrichment reads `accounts.parquet` where `band='contact'`; it does **not** read adjudicated results, so a review account the LLM approves never gets enriched. That's a real gap in the pipeline, not just the docs.

| Stage | In → Out (format, volume) | What happens, tech | Why it exists | Failure / quality | Perf / scale |
|---|---|---|---|---|---|
| **Raw** [IMPLEMENTED: given] | Shodan JSONL.zst, 12.4GB | Source snapshot 2026-09-14 | Raw truth | Corruption undetected (no checksum) | n/a |
| **Ingestion** | zst → JSON, 84.6GB | Python `zstandard` stream copy (`copy_stream`), constant memory | DuckDB reads the JSON, not zst | `zstandard` is not in requirements.txt. Partial output if interrupted | Streaming, disk-bound |
| **Bronze** | JSON → `manifest.json` | Size, mtime, **estimated** rows | Reproducibility without a copy | Estimate was off 2x. No hash | Free |
| **Silver hosts** | JSON → `hosts.parquet`, 8.9M rows, 386MB | DuckDB `read_json`, explicit projection 20 of 60+ fields, nested structs flattened, tag flags derived, ZSTD Parquet | Typed, small, fast to query | `ignore_errors`, `try_cast(ts)` silently NULL. 25MB record needed raised object limit. Flags NULL when `tags` NULL (33%) | 8 threads, 4GB cap. Linear in raw size |
| **Normalization** (in ER) | `domains`/`org` → `company_key` | lowercase, trim, strip trailing punctuation | Same company, one key | Punctuation split ("meteverse limited." bug) | SQL scalar functions |
| **Entity resolution** | hosts → tier per row | domain tier / org tier / unresolved, denylists, PTR regex | Hosts are not companies | False merges (comcast.net), unmeasured accuracy | One full scan + LIKE chains |
| **Company mapping** | → `companies.parquet` (189,257), `host_company_map.parquet` | group by (company_key, tier), `host_count`, infra flag ≥500 | Company grain + traceability back to hosts | Key collision across tiers (fixed with composite key) | Group-by over 8.9M |
| **Security signals** | hosts ⨝ map → `security_signals.parquet` | per-row CVE/EPSS/EOL/port facts, aggregated per company | Facts separate from judgment | NULL-unsafe join (fixed). Counts are per-observation (double counting) | Join of 8.9M x 1.9M on 3 columns |
| **Enrichment** | top 25 contact+domain → `contacts.jsonl` | Snov.io (Hunter.io attempted, switched) | Reps need a person | Quota, 8 of 25 no contact, no retry | 1 req/s, tiny |
| **Scoring** | companies ⨝ signals → `accounts.parquet` | fit + urgency → 0.5/0.5 → band, `why[]` | Rank + explain | Weights uncalibrated | Single scan |
| **LLM adjudication** | review band → `accounts_adjudicated.parquet` | prompt v1, gpt-oss-120b via Groq, label/rationale/confidence | Judgment where thresholds are weak | 5 of 13,159 run. Parse failure → `escalate` | Serial, 12.8s/call |
| **Gold** | the parquet files | accounts, signals, adjudicated, contacts | Consumer-ready | No contract or tests | Small (9MB) |
| **Consumers** | FastAPI `/api/chat`, `/api/stats`, UI, chatbot | DuckDB read per request | Rep-facing | SQL injection, no auth | Fine at 189K rows |

---

# STEP 2: Module by module

Each module: one-liner, in→out, why/alternative, fails (detect), test, scale, senior change, Suresh asks.

### `scripts/decompress_source.py` [IMPLEMENTED]
- One-liner: stream-decompress the zst to JSON. In: 12.4GB. Out: 84.6GB.
- Why: DuckDB needs the plain file. Alternative: let DuckDB read compressed JSON directly or decompress in-flight, avoiding 84.6GB of disk.
- Fails: disk full, interrupted copy leaves a truncated file. Detect: line count vs expected, compare to a hash. Test: round trip a small file. Scale: no change. Senior: write to temp and rename, verify a checksum.
- Suresh: "Why decompress to disk at all?"

### `pipeline/bronze/manifest.py` [IMPLEMENTED]
- One-liner: records source identity. Out: `manifest.json` with size, mtime, estimated rows.
- Why: Bronze is the raw file. Alternative: SHA-256 and an exact count. The estimate used a fixed `AVG_LINE_BYTES` and was wrong by 2x because random sampling is length-biased.
- Fails: file replaced with same size. Senior: hash, exact count, row-group-level checks. Suresh: "A manifest with a guessed row count, why?"

### `pipeline/bronze/s.py`, `sample.py` [IMPLEMENTED, scratch]
- Schema peek with `read_json_auto` on 5000 rows, and a 5000-line sample file. Exploration only. Not pipeline code. Say so if asked. Sampling the first N lines is not a random sample (file order may be biased).

### `pipeline/silver/hosts.py` [IMPLEMENTED] ← most important
- In: 84.6GB JSON. Out: 8.9M-row Parquet, 386MB.
- Inside: explicit `columns=` projection so DuckDB parses only what you named; `location.country_code` etc. flattened; `list_contains(tags,'eol-product')` flags; ZSTD.
- Why: auto-detect on 60+ heterogeneous device structs is slow and fragile. Alternative: Spark `from_json` with schema, or pre-split files.
- Fails: `ignore_errors` drops bad rows silently, bad timestamp → NULL silently, schema field renamed by provider → silently NULL column, flags NULL when tags is NULL. Detect: compare row count to line count (you did it manually), null-rate report per run, alert on change.
- Test: tiny fixture JSONL with a bad line, a NULL tags row, a missing field. Assert counts.
- Scale: 10x fits one node. 100x needs partitioned input and parallel readers. JSON parsing is the cost, not Parquet writing.
- Senior: surrogate `host_id`, count and quarantine rejects, `snapshot_date` column, write to temp then atomic rename.
- Suresh: "What does `ignore_errors=true` do to your data trust?"

### `pipeline/silver/sanity_check.py` [IMPLEMENTED]
- Prints null rate, distinct counts, tag rates, CVE and domain coverage. Never fails. Not a gate.
- Senior: turn each into an assertion with a threshold (null rate drift, row count vs prior run), write results to a table. 22 separate scans of the file; one pass is enough.

### `pipeline/entity_resolution/denylists.py` [IMPLEMENTED]
- Three frozensets (shared infra domains, brand domains, placeholders), plus keyword lists for hyperscaler and generic ISP.
- Note: the SQL uses **exact** `NOT IN` on the domain list. The Python helpers (`is_shared_infra_domain`, etc.) use suffix matching but are **unused**; don't present them as part of the pipeline. Keyword lists use substring `LIKE '%k%'`, so "ovh", "azure", "mobile", "cable" can false-match inside unrelated names.
- Senior: version the lists as data (seed table), add a review log of what was added and why, test for false matches.

### `pipeline/entity_resolution/resolve.py` [IMPLEMENTED] ← most important
- In: `hosts.parquet`. Out: `companies.parquet`, `host_company_map.parquet`.
- Inside: `candidate_domain` = first entry of `domains` that survives the filters (not denylisted, not `.arpa`, has a real dot after stripping trailing dot); `is_dynamic_ptr` = regex on the first hostname; tier = domain if candidate and not dynamic PTR and org/isp isn't hyperscaler; else org if org isn't hyperscaler/ISP; else unresolved; key = lowercase domain or org with trailing punctuation stripped. Aggregates `mode()` for primary fields, flags `host_count >= 500`.
- Why: precision over recall. Alternatives: fuzzy matching, a company registry, ML clustering.
- Fails: see Step 5 (first-domain order dependence, ISP customers, no accuracy measure). Detect: top-N QA by host_count, which is how you found most of the 12 bugs.
- Test: a fixture table of ~30 hand-picked hosts with expected tier and key, including every bug you found (the 12 issues become regression cases).
- Scale: one scan, group-by. At 100x needs distributed group-by, but the logic is embarrassingly parallel by host row. The group-by on `company_key` can skew (a few huge keys).
- Senior: blocking keys + pairwise scoring, golden set, review queue for borderline merges.
- Suresh: "How do you know it's correct?"

### `pipeline/gold/security_signals.py` [IMPLEMENTED]
- In: hosts + map. Out: per-company signals.
- Inside: join on `(ip_str, port, ts)` with `IS NOT DISTINCT FROM` for ip; per-host max CVSS/EPSS from the `vulns` map; recent-CVE regex `CVE-202[5-9]-` (hard-coded years, ages badly); `CAST(coalesce(sum(...),0) AS BIGINT)`.
- Fails: join drops rows if the key isn't NULL-safe (happened); counts double-count repeated observations (see Step 5); `max(screenshot_text)` is a lexicographic max, not "best".
- Test: company with 2 observations of the same IP, assert `cve_count` equals distinct CVEs.
- Senior: dedupe to latest observation per (ip, port) first, count distinct CVE IDs, and use a surrogate key for the join.

### `pipeline/gold/scoring.py` [IMPLEMENTED]
- Fit (max 100): +40 domain tier, +0..20 size (saturates at 20 observations), +20 if database tag else 5, +20 if target country else 10. Urgency: 25×max EPSS, +10 recent CVE, +10 verified CVE, +15 EOL, +10 self-signed, +20 risky port, **max 90 without override**; c2 or screenshot evidence → 100. Score = 0.5 fit + 0.5 urgency. Band: honeypot or infra → exclude; ≥85 contact; ≥50 review; else skip. `why[]` array.
- Why: deterministic and explainable. Alternative: learned model (no outcome labels, so not possible).
- Fails: the weights are opinion; one honeypot host excludes a whole company; `LEAST(25*epss,25)` clamp is redundant since EPSS ≤ 1. 35% of the contact band (54 of 155) comes from the override, so a reviewer should know the band is partly evidence-driven, not score-driven.
- Test: table-driven unit tests with synthetic signal rows and expected bands, boundary values at 49/50/84/85.
- Senior: configuration table for weights, versioned, backtested against conversions.

### `pipeline/gold/adjudicate.py` [IMPLEMENTED, run on 5 rows]
- In: review-band rows. Out: `accounts_adjudicated.parquet` with `final_label`, rationale, confidence; non-review rows pass through with `final_label = band`.
- Inside: fills the prompt, calls Groq with retry on rate limit (8s×attempt, 5 tries), strips code fences, `json.loads`, on failure returns `escalate`. Writes traces. Joins results back with `USING (company_key)`.
- Fails: serial (12.8s × 13,159 ≈ 47 h), no timeout, results in memory until the end (crash = total loss), label not validated against the allowed set, echoed key not checked, join key not composite, parse failures masked as `escalate`, only handles RateLimitError.
- Test: fake client returning good JSON, fenced JSON, garbage, a wrong label, 429 then success.
- Scale: concurrency with a rate limiter, batch API, checkpoint after every N rows, an input-hash cache.
- Suresh: "What happens at call 12,000?"

### `evals/run_eval.py` + `evals/account_scoring/` [IMPLEMENTED]
- One-command harness: 28 labeled → predict → per-label TP/FP/FN, precision, recall, F1, accuracy, writes `eval_report_v1.json`, logs traces.
- Results: accuracy 78.6%; contact P 0.76 R 1.0; skip P 1.0 R 0.375; escalate P 0.75 R 0.75. The model over-assigns "contact".
- Weaknesses: n=28; the 3 few-shot examples are in the eval set; labels follow the same rule the prompt encodes (circular); F1 for classes is on 8 and 4 examples; no confusion matrix, no confidence interval, no per-example diff between versions; `prev` comparison only works within the same version file, so v1 vs v2 isn't compared.
- Test: the harness itself has no tests. Add a fixture with a stub model.
- Senior: held-out set separate from few-shots, double labeling and agreement, multiple runs for consistency, regression gate in CI.

### `llm/tracing.py` [IMPLEMENTED]
- Appends one JSONL record per call: ts, call_id, skill, prompt_version, model, tokens, latency, cost (from an approximate price table), request, response, decision.
- Weak: no `run_id`, no status/error/retry/timeout/validation-failure fields, no append-safety under concurrency, stores full request (account data) in plain text, pricing table is approximate and hard-coded. Evidence it mattered: batch 1 of the traces has 28 of 43 unparseable responses and no way to tag them other than by timestamp.

### `prompts/account_scoring/v1.md`, `skills/account-scoring/SKILL.md` [IMPLEMENTED]
- Prompt: rule list (corroboration, 2+ signals → contact, escalate cases), JSON-only output, 3 few-shot examples. Versioned in git with a changelog.
- Weak: the "rule" is almost deterministic, so a plain SQL rule could do the same (be ready: why LLM at all? Answer in Step 6); no structured-output / JSON-mode enforcement; no instruction on out-of-range inputs.

### `pipeline/enrichment/snov_enrich.py`, `hunter_enrich.py` [IMPLEMENTED]
- Top N contact-band, domain-tier by score → domain search → pick contact with a security-ish title else highest confidence. 17 of 25 found.
- Weak: no retry or backoff, HTTP errors recorded as no contact, no caching (reruns re-spend quota), `sleep(1)` as rate limiting, the code comment admits the Snov endpoint shape was written from memory, contacts stored in git, PII with no retention policy, doesn't read adjudicated results.

### `app/chatbot.py` [IMPLEMENTED]
- Tool-calling over Gold: `query_accounts`, `get_account`, `get_contact`, small model.
- Weak: SQL built by f-string from model-controlled arguments (injection); recursion with no depth cap; `DISPATCH[...]` raises on an unknown tool; unbounded `limit`; no auth; chat history unbounded.

### `api/server.py`, `api/static/index.html`, `render.yaml` [IMPLEMENTED]
- FastAPI: `/api/chat` (stateless, full history from the client), `/api/stats`, static UI. Render free-tier config.
- Weak: `Groq()` at import time fails without a key; `0.0.0.0` bind; no auth/rate limit; the deployed app reads `accounts.parquet` (whitelisted in `.gitignore`), not the adjudicated file.

### `requirements.txt`
- Missing `zstandard` (needed by the decompress script). Unpinned lower bounds only. Minor, but it's the kind of reproducibility slip a director notices.

---

# STEP 3: Lineage: one real record, `weinor.de`

**Raw** [IMPLEMENTED]. A 12KB JSON line, 23 keys. `ip_str 213.61.165.202`, port 443, timestamp `2026-09-14T09:59:37`, `hostnames ["weinor.de","owa.weinor.de","h-213.61.165.202.host.de.colt.net"]`, `domains ["weinor.de","colt.net"]`, `org WEINOR GMBH AND CO KG`, `isp COLT Technology Services Group Limited`, `asn AS8220`, `product Microsoft HTTPAPI httpd`, country DE.

**Bronze.** Nothing happens; the line stays in the 84.6GB file. The manifest says the file exists.

**Silver.** `hosts.py` keeps ~20 fields, drops the 12KB down to a few hundred bytes (386MB / 8.9M ≈ 43 bytes/row compressed). The timestamp becomes a real TIMESTAMP, `domains` stays a list.

The IP was scanned **twice** (09:59 and 10:06), and a sibling IP `.200` twice too, so 4 silver rows exist for this company, but only **2 distinct IPs**.

**Normalization + ER.** `domains` → filter list → `weinor.de` survives (first), `colt.net` also survives the denylist (it isn't on it). `hostnames[1] = weinor.de` doesn't match dynamic-PTR patterns; org/isp ("WEINOR…", "COLT…") don't contain hyperscaler keywords → tier = **domain**, key = `weinor.de`.

**Company mapping.** 4 map rows (ip, port, ts, key, tier). Company row: `host_count 4`, DE, not infra.

**Security signals.** Join back to hosts via (ip, port, ts) → the `.200` rows have EOL = true and 2 CVEs each. Aggregate: `cve_count 4` (2 distinct CVEs counted twice), `eol_count 2` (1 host counted twice), max CVSS 7.5, max EPSS 0.99999, recent CVE true.

**Scoring.** Fit 69 = 40 (domain) + 4 (4/20 × 20) + 5 (no database tag) + 20 (DE is a target country). Urgency ≈ 50 = 25 (EPSS) + 10 (recent) + 15 (EOL). Score 59 → **review**. `why[]` has 3 sentences.

**LLM adjudication.** Would be sent to the model (it's in the review band). In the current data: not adjudicated, `final_label = review`, rationale null.

**Enrichment.** Not eligible (not contact band).

**Consumers.** Appears in `/api/stats` and the chatbot as a review-band account.

Say it out loud: "one host row survives as one of four observations, becomes one company, gets 3 explained signals, scores 59, lands in the LLM review band."

Other paths to know: an unresolved row (hyperscaler tenant): Silver → tier unresolved → counted, not written to the map, never scored. A c2-flagged row: signals count → urgency override to 100 → contact band if fit ≥ 70.

---

# STEP 4: The 10 strongest decisions (30-second versions)

1. **Bronze in place with a manifest.** Problem: 84.6GB, no disk headroom, 40+ irrelevant structs. Alternative: a Parquet mirror. Trade-off: reparsing if the schema changes. Production: immutable object-store Bronze, checksum, snapshot partitions. *30s:* "Mirroring the raw file would have doubled 84.6GB for no downstream benefit, so Bronze is the raw file plus a manifest, and I read it with an explicit projection. The cost is that a new field means reparsing JSON. In production I'd land immutable partitioned Parquet with checksums."
2. **DuckDB with explicit schema, not Spark.** Result: 84.6GB → 386MB, single node, no infra. Trade-off: no HA, single machine. Production: switch when the working set or recovery-time target outgrows one node.
3. **Three-tier ER with "unresolved" as a first-class bucket.** 78.3% unresolved by design: most IPs are hyperscaler tenants where the business isn't observable. Trade-off: lower recall, never measured. Production: golden set and P/R.
4. **Composite key and NULL-safe joins, refusing "close enough" counts.** The 186,435 vs 187,007 mismatch exposed two bugs. Production: automated reconciliation gates.
5. **Flag, don't drop, implausible entities (`host_count ≥ 500`).** 237 companies held ~40% of resolved volume at the time. Class-of-problem checks beat longer denylists. Trade-off: threshold is a guess, and counts observations not IPs.
6. **EPSS over CVSS, plus verified CVEs and screenshot OCR.** Uses free pre-computed signals instead of building a scanner or vision pipeline. Trade-off: CVEs are CPE-inferred.
7. **Rules decide ~93%; the LLM only sees the borderline band.** Auditability and cost. Honest: today the prompt's rule is nearly deterministic, so the LLM's added value over SQL isn't proven. Production: measure LLM vs a SQL rule baseline.
8. **Explainable scores: `why[]` and facts vs judgments in separate tables.** A rep can see which rows produced the number. Production: lineage from `why` to host rows.
9. **Prompt versioning + eval harness + tracing before scaling LLM use.** Cheap to build, and the traces caught the truncation bug. Trade-off: small, circular eval. Production: held-out golden set, gates in CI.
10. **Model per task.** Retrieval chatbot on the small model, judgment on the larger one. Trade-off: not benchmarked, just reasoning from the cost model.

Bonus story (use it): the NULL three-valued-logic bugs (`NULL LIKE` poisons `OR`/`NOT`; `NULL = NULL` unknown; `SUM` of NULL is NULL) found three separate times, plus the Parquet HUGEINT→DOUBLE downcast.

---

# STEP 5: Weaknesses and what Suresh could attack

Tags: [NEW] = found while preparing, not in your docs.

**Data correctness**
1. **[NEW] Counts are per observation, not per host.** 8.9M rows but 3.09M distinct IPs. Resolved: 1,935,076 observation rows vs 1,272,567 distinct IPs; 31,223 of 189,257 companies have repeated observations. So `host_count`, `cve_count`, `eol_count`, "N host(s)" in `why[]`, the fit size term, and the `≥500` infra threshold all inflate. *weinor.de says "4 known CVEs, 2 EOL hosts" but is 2 CVEs on 1 EOL host.* Correct answer: "I found it while tracing a record. Fix: dedupe to the latest observation per (ip, port) in Silver, count distinct CVE IDs. It changes explanation text and some scores, but it does not change the architecture." Own this; it shows maturity.
2. **[NEW] First-surviving-domain is order-dependent.** For `weinor.de` the `domains` list was `["weinor.de","colt.net"]`; `colt.net` (the ISP) is not on the denylist, and exists as its own company with 243 rows. 531,657 rows have more than one domain. If the order flipped, the company becomes the ISP. Fix: pick by evidence (matches hostname first label / org similarity), and score candidates.
3. **No ER accuracy measurement.** "Precision over recall" is a stance, not a result. Answer: "Not measured; here's how I'd measure" (sample 200 per tier, label correct/incorrect, report precision with interval; recall needs a known-entity list).
4. **Org tier is weaker than domain tier.** The org is the IP block owner, and can be a hosting reseller. Org-tier accounts aren't contactable (no domain).
5. **Hard-coded and approximate pieces:** target countries, risky ports, recent-CVE regex years, weights, 500-threshold, the 'tag' flags trust Shodan.

**Reliability**
6. No atomic writes, no run_id, full overwrite, no retries on stages, no orchestration, no automated gates. Ask: "What happens if `scoring.py` dies mid-write?" Truth: corrupted or partial Parquet at the final path.
7. **Silent loss:** `ignore_errors`, `try_cast`. Truth: the count matched once, by manual check.
8. **Idempotency:** reruns are idempotent only because everything is a full refresh. Say that, not "idempotent by design."

**Performance / scale**
9. Single node, observed runtime unverified (README says ~90s; unverified).
10. The join on 3 columns incl. timestamp is wide; a `host_id` would be cheaper.
11. LLM stage: 47 hours serial, 12.8s/call, 1.9k tokens per call, no concurrency.

**LLM**
12. 5 of 13,159 adjudicated; 28-example eval; leakage of 3 few-shots; rule-circular labels; over-labels "contact"; unparseable → escalate masks failures; no validation of the output schema; "confidence" is self-reported and never calibrated; no baseline versus a SQL rule. Strong answer for "why an LLM": "Honestly, for the rule as written, SQL could do it. The LLM earns its place only where the rule can't be written (name looks like an ISP, fit vs evidence trade-offs). I haven't proven that yet; the baseline comparison is the next experiment."
13. Cost doc stale (Claude, 600/75 tokens). Real: 1.9k/0.46k tokens, ≈$0.0006/call, ≈$8 for the band by your own price table (approximate).

**Testing / maintainability**
14. No tests of any kind, absolute `D:\` paths, no config layer, no package structure, docs drift (Hunter/Claude vs Snov/Groq).

**Security**
15. Chatbot SQL injection via f-strings of model-controlled args; no auth; contacts (PII) committed in git; `.env` is correctly ignored.

**Product logic**
16. LLM-approved review accounts never reach enrichment.

---

# Next: the interview (Step 6)

The final deliverables (50 questions, module one-liners, 2/5/10-minute explanations) I'll write at the end, once the interview shows what you actually can't explain yet.
