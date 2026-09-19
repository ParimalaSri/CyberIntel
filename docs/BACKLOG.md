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
- [x] Resolution-yield report: **187,007 resolved companies** out of 8.9M
      host rows — 9.0% domain-tier, 13.0% org-tier, 78.0% unresolved
      (expected and by design: precision over recall, and this dataset's
      `org`/`isp` fields skew heavily toward hyperscaler/CDN tenant IPs)
- [x] Fixed on review: `company_key` punctuation normalization (merged 181
      duplicate rows, e.g. "meteverse limited" vs "meteverse limited.") and
      a `likely_infra_or_proxy` flag (org-tier, host_count >= 500 - no real
      single business owns that many IPs with no backing domain). Flags
      237 companies (0.13% of resolved companies) holding **780,194 hosts —
      ~40% of all resolved host volume** — confirms these would have
      dominated any volume-based scoring signal if left untreated.

## Sprint 3 — Scoring & Gold
**Epic:** As an SDR, I want every company ranked by fit x urgency with the
reasons spelled out, so I know who to call and why.
- [x] Security signals table (`pipeline/gold/security_signals.py`) — CVE
      count/max CVSS/max EPSS/recency, EOL/self-signed/honeypot/c2 counts,
      risky-open-port count, attack-surface size. 187,007 rows, matches
      `companies.parquet` exactly after fixing two join-correctness bugs
      (composite key collision, NULL-unsafe join on `ip_str`) found via
      row-count QA. 31,261 companies have >=1 CVE; 5,398 have a risky open
      port; 58 show the `c2` tag (possibly-compromised bucket).
- [x] Company metadata folded directly into scoring (host_count, reachability,
      `database_tag_count`-based vertical bonus already lived in
      `companies.parquet`/`security_signals.parquet` - a separate
      intermediate table wasn't worth the extra pipeline stage). Note:
      dropped the originally-planned `ics`/`medical` tag vertical bonus -
      those tags are near-nonexistent in this dataset (single digits out of
      8.9M rows) - swapped for `database_tag_count` instead, which we
      actually have reliable coverage for.
- [x] Scoring rules (`pipeline/gold/scoring.py`) — fit_score (reachability +
      size + vertical + geography) x urgency_score (CVE/EPSS + EOL +
      self-signed + risky ports, with `c2` overriding to max urgency) ->
      contact_score, banded contact/review/skip/exclude.
- [x] `data/gold/accounts.parquet` — 187,106 rows + `why[]` explanation array.
      Band distribution: 202 contact (0.1%), 13,562 review (7.2%), 526
      exclude (0.3%), 172,816 skip (92.4%).
- [x] Found and fixed 3 issues via QA before accepting the output: (1)
      Parquet has no native 128-bit int, so DuckDB's SUM() silently
      downcast to DOUBLE, printing "16.0 hosts" in customer-facing why[]
      text - fixed with explicit CAST to BIGINT; (2) `amazon.com` resolved
      as a fake "contact"-band prospect - its real org was NTT America,
      flagged `c2`, almost certainly a spoofed reverse-DNS PTR on
      malicious infrastructure impersonating the brand (PTR records are
      owned by the IP block owner, not the real domain owner, so they're
      trivially spoofable) - added a brand-domain denylist; (3)
      `'localhost.'` had aggregated 2,375 unrelated misconfigured hosts
      into one fake company - added a reserved/placeholder domain denylist.

## Sprint 3.5 — Gold hardening (found via user cross-check + QA)
**Epic:** Before trusting Gold enough to build Sprint 4 on top of it, close
the remaining known gaps and add the one genuinely free signal we'd missed.
- [x] **Screenshot evidence** (`screenshot.labels`/`screenshot.text`) added to
      Silver Hosts. Shodan pre-computes OCR text and scene labels on exposed
      RDP/VNC screenshots (~0.03% of rows) - no vision model needed, the
      analysis is already done. Surfaced as `has_screenshot_evidence` in
      Security Signals (urgency override, same tier as `c2`) and as the
      top-priority `why[]` entry in Gold. Real examples now in the contact
      band: actual OCR'd employee usernames off exposed Windows RDP login
      screens.
- [x] **Cross-checked every other dropped field** for enrichment value
      (`ntlm`, `mongodb.authentication`, `redis.authentication_required`,
      `ftp.anonymous`, `http.html`) - all either too rare (<0.25% of rows) or
      too noisy (NTLM mostly shows auto-generated cloud-VM hostnames, not
      real corporate domains) to be worth the added complexity. One cheap
      win taken: `vulns.verified` (already in our schema, unused) surfaced
      as `verified_cve_count` - distinguishes a human-confirmed CVE match
      from an automated CPE-based guess.
- [x] Extended `likely_infra_or_proxy` to **domain-tier**, not just org-tier
      (`comcast.net`, `spectrum.com`, `sakura.ne.jp` were only excluded by
      the luck of also having a honeypot tag - now caught by host_count
      >= 500 regardless of tier).
- [x] Added `.arpa` reserved-zone denylist (`37.in-addr.arpa` was sitting in
      the contact band with a perfect 100 score).
- [x] Generalized the dynamic-PTR detector: originally only caught
      dash-separated IP-in-hostname patterns
      (`77-163-107-27.fixed.kpn.net`); missed dot-separated ISP-customer
      patterns like `179.202.41.212.static.wline.lns.sme.cust.swisscom.ch`
      (437 of Swisscom's own broadband customers, aggregated under
      "swisscom.ch" as if Swisscom itself were the prospect). Now also
      matches dotted-IP prefixes and explicit ISP markers
      (`static`/`dynamic`/`cust`/`pool`/`dsl`/`dial`/`dhcp`).
- [x] Added a domain-plausibility check: `candidate_domain` must contain a
      real dot after stripping any trailing one. Caught `'vds.'` (81 hosts
      aggregated under a hosting provider's internal VPS-naming suffix,
      not a real registrable domain) on the first pass, but the naive
      `LIKE '%.%'` check missed it since `"vds."` technically contains a
      dot - fixed by stripping the trailing dot before checking.
- [x] Final numbers after this round: **189,257 resolved companies**
      (7.5% domain-tier, 14.2% org-tier, 78.3% unresolved). Band
      distribution: 155 contact, 13,159 review, 662 exclude, 175,281 skip.
      Accepted as good enough to build on - remaining noise is a documented,
      known limitation, not a blocker.

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
