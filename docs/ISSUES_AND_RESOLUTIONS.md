# Issues & Resolutions

This is a consolidated log of every data-quality and correctness issue found
while building the pipeline, in the order they were found — not a sanitized
retrospective. Each one follows the same shape: what broke or looked wrong,
why, how it was fixed, and how the fix was verified. Details and full commit
messages live in [BACKLOG.md](BACKLOG.md); this doc pulls them into one
narrative.

**Why this exists:** working real internet-scan data surfaces bugs you can't
predict from the schema alone — SQL's NULL semantics, Parquet's type system,
and the dataset's own messiness each produced a real, found-not-hypothetical
bug. This log is the evidence trail.

---

## 1. Ingestion — a record bigger than the parser's default limit

**Found:** the very first full run of `pipeline/silver/hosts.py` failed
outright with `"maximum_object_size" of 16777216 bytes exceeded`.

**Root cause:** one record (likely carrying a large embedded screenshot or
HTML blob) is ~25MB — DuckDB's `read_json` defaults to a 16MB per-object cap.

**Fix:** raised `maximum_object_size` to 128MB in the `read_json()` call.

**Verified:** full 84.6GB scan completes, 8,914,693 rows written, matching
the raw line count exactly.

---

## 2. Entity resolution — NULL silently mis-buckets rows

**Found:** QA-sampling the "unresolved" bucket surfaced
`('ACEVILLE PTE.LTD.', None, 84972)` — a real, non-hyperscaler company
landing in the bucket meant for cloud/CDN tenant IPs.

**Root cause:** the hyperscaler check was
`lower(isp) LIKE '%google%' OR ...`. In SQL, `NULL LIKE anything` evaluates
to `NULL`, not `false` — and `NULL` poisons the surrounding `OR`/`NOT`
chain. Any row with a missing `isp` (8.6% of rows) silently fell through to
`unresolved` regardless of what `org` said.

**Fix:** wrapped in `COALESCE(lower(isp), '')` so a missing value compares
as empty string (definitively false) instead of unknown.

**Verified:** unresolved dropped from 80.0% to 78.0% of rows; ACEVILLE now
correctly resolves to org-tier.

---

## 3. Entity resolution — punctuation splits one company into two

**Found:** `'meteverse limited'` (78,292 hosts) and `'meteverse limited.'`
(62,702 hosts) sitting as two separate top companies — same business, one
trailing period apart.

**Root cause:** the org-tier key was `lower(trim(org))` — trims whitespace,
not trailing punctuation.

**Fix:** `regexp_replace(lower(trim(org)), '[\s.,]+$', '')`.

**Verified:** merged into one row, 140,994 hosts (78,292 + 62,702, exact).

---

## 4. Entity resolution — proxy/hosting networks mistaken for one company

**Found:** top org-tier "companies" included `ACEVILLE PTE.LTD.` (130,066
hosts across 3 ASNs), `Internet Rimon` (92,769 hosts) — no real single
business owns that many IPs.

**Root cause:** these are proxy/hosting networks whose names don't contain
any denylist keyword — a keyword-based denylist is incomplete by
construction.

**Fix:** flagged (not dropped) org-tier rows with `host_count >= 500` as
`likely_infra_or_proxy`, kept for transparency, discounted in scoring.

**Verified:** 237 flagged companies (0.13% of resolved companies) held
780,194 hosts — ~40% of all resolved host volume. Confirms these would have
dominated any un-flagged volume-based signal.

---

## 5. Security Signals — a row-count mismatch traced to two separate bugs

**Found:** `security_signals` produced 186,435 rows against
`companies.parquet`'s 187,007 — insisting the two match exactly (not
"close enough") surfaced two compounding bugs.

**Bug (a) — key collision:** joining/grouping on `company_key` alone let an
unrelated domain-tier and org-tier key collide if the strings happened to
match. **Fix:** use the composite `(company_key, resolution_tier)`
everywhere downstream.

**Bug (b) — NULL-unsafe join:** `ip_str` is NULL on 1.2% of rows; SQL's
`NULL = NULL` is unknown, not true, so those rows never joined — any
company whose only hosts had a NULL `ip_str` vanished from the output
entirely. **Fix:** join on `(ip_str, port, ts)` using `IS NOT DISTINCT
FROM` for `ip_str` (NULL-safe equality); microsecond-precision `ts` makes
collisions among NULL-ip rows effectively impossible.

**Verified:** counts now match exactly, 187,007 = 187,007.

---

## 6. Gold — Parquet has no 128-bit integer, numbers silently became floats

**Found:** `why[]` text read *"16.0 host(s) running end-of-life software"*
instead of "16"; a sanity query showed `('hurricane electric', ..., None,
None, 0.0)` — counts that should never be NULL.

**Root cause, part 1 (the NULL):** `is_eol_product::INT` is itself NULL
whenever a host's `tags` field is NULL (33.1% of rows) —
`list_contains(NULL, 'x')` returns NULL, not false, and `SUM()` of an
all-NULL group returns NULL, not 0.

**Root cause, part 2 (the float):** DuckDB's `SUM()` computes internally as
HUGEINT (128-bit); Parquet has no native 128-bit integer type, so it
silently downcasts to DOUBLE on write.

**Fix:** `CAST(coalesce(sum(...), 0) AS BIGINT)` on every count aggregate.

**Verified:** clean integers throughout; "16 host(s)", not "16.0 host(s)".

---

## 7. Gold — a fake "contact"-band prospect that was actually a spoofed brand

**Found:** `amazon.com` sitting in the contact band with a perfect score of
93.

**Root cause:** its resolved `org` was `NTT America, Inc.`, not Amazon, and
the host was flagged `c2` (possible command-and-control). Almost certainly
a spoofed reverse-DNS PTR record impersonating the brand — PTR records are
controlled by whoever owns the IP block, not by the real domain's
registrant, so they're trivially fakeable.

**Fix:** added a `BRAND_DOMAINS` denylist (amazon.com, google.com,
microsoft.com, apple.com...) — at that scale, a resolved domain is never a
genuine SMB lead, it's either infrastructure already caught elsewhere, or
impersonation.

---

## 8. Gold — a placeholder hostname aggregated thousands of unrelated hosts

**Found:** `'localhost.'` had aggregated 2,375 hosts into one fake company,
with sample hostnames like `undefined.hostname.localhost`.

**Root cause:** generic placeholder hostnames from misconfigured devices
across many unrelated networks all reduce to the same reserved domain
string.

**Fix:** added a `RESERVED_PLACEHOLDER_DOMAINS` denylist (`localhost`,
`localdomain`, `invalid`, `example.com/net/org`).

---

## 9. Gold — a reserved DNS zone treated as a company

**Found:** `37.in-addr.arpa` sitting in the contact band with a perfect
score, discovered in the same output run that validated the screenshot-
evidence feature.

**Root cause:** `.in-addr.arpa`/`.ip6.arpa` are IANA-reserved reverse-DNS
zones, not real registrable domains — same category of bug as #8, a
different reserved namespace.

**Fix:** added a suffix check, `lower(d) NOT LIKE '%.arpa'`.

---

## 10. Gold — ISPs' own broadband customers mistaken for the ISP

**Found:** `comcast.net` (2,803 hosts), `spectrum.com` (12,821 hosts),
`sakura.ne.jp` (4,597 hosts) sitting in the sample, some scoring a perfect
100 — several only excluded by the coincidence of also carrying a
honeypot-flagged host. `swisscom.ch` (437 hosts) later did the same with
zero honeypot/c2 signal to save it: its hostnames read
`179.202.41.212.static.wline.lns.sme.cust.swisscom.ch` — Swisscom's own
naming convention for its broadband customers' static IPs.

**Root cause:** `likely_infra_or_proxy` (issue #4's fix) only applied to
org-tier companies; domain-tier had no equivalent implausible-host-count
check at all.

**Fix:** extended the flag to domain-tier too (`host_count >= 500`,
regardless of tier).

---

## 11. Entity resolution — the dynamic-PTR detector was too narrow

**Found:** issue #10's `swisscom.ch` case revealed the existing
ISP-customer-PTR detector only caught dash-separated IP patterns
(`77-163-107-27.fixed.kpn.net`), missing dot-separated ones
(`179.202.41.212.static...`).

**Fix:** generalized the detector to also match a dotted-IP prefix
(`^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.`) and explicit
ISP-customer markers (`static`, `dynamic`, `cust`, `customer`, `pool`,
`dsl`, `dial`, `dhcp`) anywhere in the hostname.

---

## 12. Entity resolution — a trailing dot defeated the domain-plausibility check

**Found:** `'vds.'` (81 hosts, aggregated under a hosting provider's
internal VPS-naming suffix, not a real domain) still appeared after adding
a `LIKE '%.%'` "must contain a dot" check.

**Root cause:** `"vds."` technically contains a dot — at the very end. The
naive check didn't distinguish a real SLD.TLD structure from a bare label
with a stray trailing period.

**Fix:** `regexp_replace(lower(d), '\.$', '')` to strip a trailing dot
*before* checking for a real remaining dot.

**Verified:** both `swisscom.ch` and `vds.` gone from the top results after
this round; final count 189,257 resolved companies.

---

## Lessons that generalized across multiple issues

- **SQL's three-valued logic (NULL is not false) caused three separate
  bugs** (#2, #5b, #6) in different parts of the pipeline. Any `LIKE`,
  `SUM`, or `=` touching a nullable column needs an explicit NULL-safety
  check — this isn't a one-off gotcha, it's a pattern worth checking for
  by default now.
- **"Close enough" row counts hid real bugs.** Issue #5 was only caught by
  refusing to accept 186,435 vs 187,007 as noise and insisting on an exact
  match. Several other fixes (#3, #12) were found the same way — treating
  a summary statistic as a claim to verify, not a number to glance at.
- **A denylist is a floor, not a ceiling.** Every brand/placeholder/ISP
  fix (#7, #8, #9, #10) is the same shape: a keyword or pattern list will
  always miss cases outside its training data (in this case, "what we've
  happened to look at so far"). The mitigation isn't a perfect list, it's
  cheap, generalizable checks (domain plausibility, host-count sanity)
  that catch *classes* of the problem, plus documenting the residual
  noise as a known limitation rather than pretending it's solved.
- **Precision over recall, consistently.** Every fix in this doc trades
  some correctly-resolved companies for fewer incorrectly-resolved ones
  (e.g., #4's `likely_infra_or_proxy` flag discounts real signal from 237
  companies to avoid trusting the 40% that's actually noise). That's a
  deliberate, stated trade-off (see `ARCHITECTURE.md`), not an accident.
