# Planning: AI Sales Intelligence Platform

## Problem

A cybersecurity software company's sales team needs to know, out of thousands of
businesses, **who to contact first**. We were handed a raw Shodan internet-scan
export (~8.9M host/service observations, 84.6GB JSONL) and asked to turn it into
a prospecting tool.

## Who this is for

Primary user: an SDR/AE at the cybersecurity vendor, working a queue of accounts.
They don't want a data lake — they want a ranked list of companies, a reason each
one is ranked where it is, and a person to call.

## Use cases we chose, and why

1. **Account discovery & prioritisation (Fit x Urgency score).**
   The dataset is service/banner-level, not company-level. The core value we can
   add is rolling ~8.9M host rows up into a per-company account list ranked by
   how good a fit they are *and* how exposed they are right now. This is the
   textbook B2B account-scoring model (ICP fit score x intent/signal score) and
   it's the one thing a rules engine alone can't do well, because "fit" and
   "urgency" both require combining several weak signals.

2. **Exposure-based buying signals as the "why now."**
   EOL software, unpatched CVEs (both freshly disclosed and decade-old-and-still-
   unpatched), self-signed certs, and unauthenticated high-risk ports (VNC, RDP,
   MSSQL, Telnet) are concrete, defensible reasons to reach out — not "we think
   you might need security software" but "your MSSQL instance on port 1433 has
   no auth and CVE-2026-XXXXX is live against it." This is what makes the pitch
   land instead of reading as spam.

3. **AI adjudication at the score boundary, not everywhere.**
   Accounts that clearly qualify (score >= 85) or clearly don't (score < 50) are
   decided by rules — cheap, deterministic, auditable. Only the ambiguous middle
   band (50-84) gets an LLM read, because that's the only band where judgment
   (not another threshold) actually changes the outcome. This is the rule-vs-LLM
   split the take-home explicitly asks us to defend.

4. **Contact enrichment gated to flagged accounts.**
   We don't enrich all resolved companies (burns quota on noise) — only accounts
   that clear the contact bar. Contact data (name/email/title) isn't in the scan
   data at all, so this is a real external call (Hunter.io), not a derived field.

5. **A chatbot over the Gold layer.**
   Once Gold exists (company, score, signals, contact), the natural interface
   for a salesperson is "who should I call today and why," not a SQL console.
   This is a structured tool-use pattern (the LLM calls functions against Gold +
   the contact API), not RAG over documents — there's nothing document-shaped
   here.

## Non-goals (explicitly out of scope)

- We are not building a CRM, a sequencer, or an auto-send outreach tool.
- We are not running our own vulnerability scanner — we work from the Shodan
  export as given, plus optional light enrichment.
- We are not attempting to resolve every one of the ~8.9M host rows to a
  company. A large fraction are cloud-tenant IPs with no addressable owner —
  Silver Companies carries an explicit "unresolved" bucket instead of forcing a
  match.

## Success criteria

- A salesperson can open the app and get a ranked, explained account list.
- Every score is traceable to specific rows in the source data (no black box).
- The LLM's involvement is narrow, measured (evals), and cheaper than doing the
  same judgment call by hand at scale.
