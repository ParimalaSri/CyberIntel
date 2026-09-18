# Backlog

Living document. Agile, sprint-based. Update status inline as work completes —
this is the task tracker for the project (see [ARCHITECTURE.md](ARCHITECTURE.md)
for the "how", [PLANNING.md](PLANNING.md) for the "why").

Status legend: `todo` / `in-progress` / `done` / `blocked`

## Sprint 0 — Project setup
- [x] Repo init, folder structure, `.gitignore`
- [x] DuckDB installed
- [x] PLANNING.md / ARCHITECTURE.md / BACKLOG.md drafted
- [x] Initial commit

## Sprint 1 — Bronze & Silver Hosts
**Epic:** As a data engineer, I want the raw 84.6GB JSONL converted into
partitioned Parquet, so downstream queries don't re-parse JSON every time.
- [x] `pipeline/bronze/manifest.py` — source-file identity manifest (Bronze is
      the raw JSONL in place, not a Parquet mirror — see ARCHITECTURE.md)
- [x] `pipeline/silver/hosts.py` — flatten nested structs into a typed
      `silver_hosts` table (one row per ip:port:timestamp), explicit DuckDB
      column projection, schema-validated on a 5000-row sample
- [x] Full run against the 84.6GB source — `data/silver/hosts.parquet`,
      368.7MB, **8,914,693 rows** (corrects the earlier ~4.2M estimate, which
      was skewed by length-biased random sampling — see chat history)
- [x] Row-count / null-rate sanity report — `pipeline/silver/sanity_check.py`.
      Key coverage: `domains` populated on 73.7% of rows (entity-resolution
      backbone), `org` on 99.8%, `vulns` on 2.8% (253,096 rows with >=1 CVE),
      `product` on only 17.5% (most rows are bare port/banner scans with no
      fingerprint). Matches the earlier 1%-sample percentages closely — only
      the absolute row-count estimate was off, not the proportions.

## Sprint 2 — Entity Resolution & Silver Companies
**Epic:** As a sales analyst, I want host rows grouped into companies, so the
account list is at the grain a salesperson thinks in.
- [x] Hyperscaler/CDN/generic-ISP denylists — `pipeline/entity_resolution/denylists.py`
- [x] Resolution heuristic (`pipeline/entity_resolution/resolve.py`): domain
      tier (via `domains`, denylist + dynamic-PTR filter) -> org tier
      (non-hyperscaler org name) -> unresolved. Fixed a NULL-propagation bug
      where rows with a missing `isp` fell through to `unresolved` regardless
      of `org` (SQL three-valued logic: `NULL LIKE x` is `NULL`, not `FALSE`,
      and poisons the surrounding `OR`/`NOT` chain) — wrapped in `COALESCE`.
- [x] `silver_companies` table (`data/silver/companies.parquet`) + explicit
      `unresolved` bucket (not written to the table, reported as a count)
- [x] Resolution-yield report: **187,188 resolved companies** out of 8.9M
      host rows — 9.0% domain-tier, 13.0% org-tier, 78.0% unresolved
      (expected and by design: precision over recall, and this dataset's
      `org`/`isp` fields skew heavily toward hyperscaler/CDN tenant IPs)

## Sprint 3 — Scoring & Gold
**Epic:** As an SDR, I want every company ranked by fit x urgency with the
reasons spelled out, so I know who to call and why.
- [ ] Security signals table (CVE, EOL, self-signed, risky ports, honeypot/c2)
- [ ] Company metadata table (size, ASN type, vertical guess, geo, reachability)
- [ ] Scoring rules (fit_score, urgency_score, contact_score, band)
- [ ] `gold_accounts` table + `why[]` explanation array per row

## Sprint 4 — LLM adjudication: skill, prompts, evals, tracing
**Epic:** As a sales ops lead, I want the borderline-band AI decision to be
measured and versioned, not a black box.
- [ ] `skills/account-scoring/SKILL.md`
- [ ] `prompts/account_scoring/v1.md` (+ v2 once we iterate)
- [ ] Tracing writer (schema in ARCHITECTURE.md) wired into every LLM call
- [ ] `evals/account_scoring/labeled_set.jsonl` (20-30 hand-labeled examples)
- [ ] `evals/run_eval.py` — one-command harness, precision/recall vs. previous
      prompt version
- [ ] `docs/COST_MODEL.md` — tokens x volume x frequency, model choice, cost
      ceiling

## Sprint 5 — Contact enrichment + API
**Epic:** As an SDR, I want a name/email for flagged accounts, so I can
actually send the email.
- [ ] Hunter.io client, called only for `contact_flag=true` rows
- [ ] FastAPI: `/accounts`, `/accounts/{id}`, `/accounts/{id}/contact`
- [ ] Outreach-draft skill/prompt (optional stretch, same eval pattern)

## Sprint 6 — Chatbot
**Epic:** As an SDR, I want to ask "who should I contact today" in plain
English and get an answer with a name attached.
- [ ] Tool-calling loop over the Gold API + contact endpoint
- [ ] Minimal chat UI

## Sprint 7 — Ship
- [ ] Deploy/host the app, get a public link
- [ ] "How You Build" reflection (½-1 page)
- [ ] Final pass on all docs
- [ ] Optional Loom walkthrough
