# ROLE
You are a senior AI systems architect and full-stack engineer. Design and build a production-quality
**Multi-Agent Job Application Assistant** with strict Human-in-the-Loop (HITL) controls.
Deliver working code, not just a design.

# OBJECTIVE
Build a system that, for a single candidate (the user):
1. Connects to any job portal or website the user provides (Naukri, LinkedIn, Shine, Indeed, Foundit,
   company career pages, etc.) and authenticates as the user.
2. Stores and maintains the candidate's profile.
3. Searches recently posted jobs that match the profile, filtered by a user-chosen recency window.
4. Lists matching jobs, gets explicit user approval, then applies one by one (uploading resume,
   filling forms, answering screening questions).
5. Reports application statistics for any time window on demand.
6. Monitors the user's email for job-related messages, drafts replies, and sends them only after
   the user approves the final text.

# AGENT ARCHITECTURE
Implement these agents, coordinated by a supervisor. Each agent has a single responsibility,
a defined input/output schema, and its own tools.

1. **Orchestrator / Supervisor Agent**
   - Single entry point for the user (chat UI or CLI).
   - Interprets the request, routes to the right agent, manages workflow state.
   - Owns all HITL checkpoints: it pauses the workflow, presents a decision to the user,
     and resumes only on an explicit answer.

2. **Portal Connector & Auth Agent**
   - Accepts a portal name or any URL. Uses a plugin/adapter pattern: one adapter per known portal
     (Naukri, LinkedIn, Shine) plus a **generic adapter** that uses an LLM + browser automation to
     handle unknown sites.
   - Authentication order of preference: official API/OAuth → email + password → email OTP / magic link.
   - For OTP or magic-link login, it hands off to the Email Monitor Agent to read the code
     (only from the expected sender, only within the last 10 minutes).
   - If a CAPTCHA, 2FA prompt, or "unusual activity" check appears: STOP and ask the user to complete
     it manually in the visible browser. Never attempt to bypass CAPTCHAs or bot detection.
   - Persists sessions (cookies/storage state) encrypted, per portal, with expiry tracking.

3. **Profile Agent**
   - Builds the candidate profile from: uploaded resume (PDF/DOCX), LinkedIn/Naukri profile import,
     and a short Q&A with the user.
   - Stores a structured profile: name, contact, current role, total & relevant experience,
     skills (with years), education, certifications, current CTC, expected CTC, notice period,
     preferred locations, remote/hybrid/onsite preference, target roles, target companies,
     excluded companies, work authorization, links (GitHub, portfolio), plus multiple resume versions.
   - Maintains a **Screening Answer Bank**: reusable answers to common questions
     ("Why are you looking for a change?", "Years of experience in X", "Willing to relocate?").
     New questions encountered during applications are added after user confirmation.
   - Every profile change is versioned.

4. **Job Search Agent**
   - Asks the user for the recency window: **Last 1 hour / 24 hours / 7 days / 30 days / custom**.
   - Builds search queries from the profile (roles, skills, location, experience) and runs them across
     all connected portals.
   - Normalizes results into a common Job schema; deduplicates across portals
     (same company + similar title + same location = one job, keep all source links).
   - Skips jobs already applied to, jobs from excluded companies, and expired postings.

5. **Matching & Ranking Agent**
   - Scores each job 0–100 against the profile with a short reason:
     skills overlap, experience fit, location fit, salary fit (if shown), seniority fit.
   - Flags red flags: suspicious or scam-like postings, upfront fees, mismatched experience bands.

6. **Application Agent**
   - Applies ONLY to jobs the user approved, strictly one at a time.
   - Per job: open the posting → pick the best resume version → fill the form from the profile and
     Answer Bank → handle "Easy Apply" / multi-step forms / external redirects.
   - If a required field has no confident answer (confidence < threshold), PAUSE and ask the user;
     never invent answers about experience, salary, or qualifications.
   - Optional per-job HITL: show a filled-form preview before final submit (configurable on/off).
   - Captures confirmation (screenshot + confirmation text/ID) as proof.
   - On failure: retry once, then mark as "Failed – needs manual action" with the reason and the link.
   - Respects rate limits: randomized human-like delays, a configurable daily cap per portal
     (default 25), and stops immediately on any warning or block from the portal.

7. **Email Monitor Agent**
   - Connects to Gmail/Outlook via OAuth (read + draft + send scopes only).
   - Polls on a schedule (default every 15 min). Classifies job-related emails into: interview invite,
     recruiter outreach, assessment/test link, rejection, offer, document request, application
     confirmation, other. Ignores non-job emails.
   - Links each email to the matching application record and updates its status.
   - For any email needing action: notify the user (push/Telegram/WhatsApp/desktop/email — configurable)
     with sender, subject, summary, category, and suggested action.

8. **Reply Drafting Agent**
   - Triggered only after the user approves "draft a reply" for a given email.
   - Writes a professional, concise reply using the profile, the thread history, and the user's
     instructions (e.g., "accept, propose Thursday 3–5 PM IST").
   - If calendar access is enabled, checks availability before proposing slots.

9. **Analytics & Reporting Agent**
   - Answers questions like "stats for today", "last 7 days", "1 Oct to 15 Oct".
   - Metrics: jobs found, jobs approved, applications submitted / failed / pending, per-portal breakdown,
     responses received, response rate, interviews scheduled, rejections, offers, top matching roles,
     average match score, avg time-to-response.
   - Output both a text summary and a table; optional chart and CSV/Excel export.

# HUMAN-IN-THE-LOOP CHECKPOINTS (MANDATORY — never skip)
| # | Checkpoint | What the user sees | Allowed responses |
|---|-----------|--------------------|-------------------|
| H1 | Portal login issues | CAPTCHA/2FA/OTP not found | "Done" after manual step / cancel |
| H2 | Recency choice | 1h / 24h / 7d / 30d / custom | Select one |
| H3 | Job approval | Ranked table: #, title, company, location, exp, salary, posted, portal, match %, reason, link | Approve all / approve by numbers (e.g. "1,3,5-8") / reject / "show more details on #4" |
| H4 | Unknown form field | Question + suggested answer + confidence | Accept / edit / skip this job |
| H5 | (Optional) Pre-submit preview | Filled form summary | Submit / edit / skip |
| H6 | Important email arrived | Summary + category + suggested action | Draft reply / ignore / mark done |
| H7 | Reply approval | Full draft (To, CC, Subject, Body, attachments) | Approve & send / edit (free text) / regenerate / discard |

Rules:
- Nothing is submitted or sent without the relevant approval.
- An email is sent ONLY after H7 approval of that exact text. If the user edits, show the edited
  version again for final approval.
- Every approval is logged with timestamp and the exact content approved.

# DATA MODEL (minimum)
- `candidate_profile` (versioned), `resume_versions`, `answer_bank`
- `portal_accounts` (portal, auth type, encrypted credentials/session, status, last_login)
- `jobs` (normalized fields, source_urls[], posted_at, fetched_at, match_score, match_reason, status)
- `applications` (job_id, portal, resume_used, answers_given, submitted_at, status, proof_path, error)
- `emails` (message_id, thread_id, from, subject, category, linked_application_id, action_status)
- `drafts` (email_id, versions[], approved_version, sent_at)
- `audit_log` (every agent action and every HITL decision)

Application status lifecycle:
`found → approved → applying → applied | failed | skipped → viewed → shortlisted → interview → offer | rejected`

# SECURITY & COMPLIANCE
- Credentials and session files encrypted at rest (e.g., OS keychain or Fernet/AES with a master key);
  never logged, never sent to the LLM.
- Least-privilege OAuth scopes; tokens refreshable and revocable.
- Respect each portal's Terms of Service and robots rules. Prefer official APIs/partner programs
  where available. Make it clear in the README that automated applying may violate some portals'
  ToS (e.g., LinkedIn) and the user is responsible for enabling those adapters.
- No CAPTCHA solving, no proxy rotation to evade detection, no fake accounts.
- PII stays local by default; only the minimum needed text is sent to the LLM.
- A global **kill switch** command ("stop everything") halts all agents immediately.

# TECH STACK (use unless there's a strong reason not to; justify any change)
- Python 3.11+
- Orchestration: LangGraph (stateful graph with `interrupt()` for HITL, checkpointer for resume)
- LLM: Claude via Anthropic API (tool use / structured outputs)
- Browser automation: Playwright (headed mode available for manual steps; persistent contexts per portal)
- Storage: PostgreSQL (or SQLite for local mode) + SQLAlchemy; files on local disk
- Email: Gmail API / Microsoft Graph via OAuth
- Scheduling: APScheduler (job search refresh, email polling)
- Notifications: pluggable (Telegram bot, desktop notification, email)
- UI: Streamlit or a simple FastAPI + web chat; CLI fallback
- Config: `.env` + `config.yaml` (daily caps, polling interval, thresholds, enabled portals)

# DELIVERABLES
1. Architecture diagram (Mermaid) showing agents, tools, data stores, and HITL points.
2. Full source code, organized by agent, with a portal adapter interface and at least these adapters:
   Naukri, LinkedIn, Shine, Generic (LLM-driven).
3. Database schema + migrations.
4. Prompts for each agent stored as separate, editable files.
5. README: setup, OAuth configuration, adding a new portal adapter, configuration options, ToS note.
6. Tests: unit tests for matching, dedup, stats; mocked integration tests for apply and email flows.
7. A demo script walking through: add portal → build profile → search (7 days) → approve 3 jobs →
   apply → receive interview email → draft → approve → send → "show stats for today".

# ACCEPTANCE CRITERIA
- No application is submitted and no email is sent without a logged approval.
- Duplicate jobs across portals appear once in the approval list.
- The system never applies to the same job twice.
- Stats for any custom date range match the raw records exactly.
- If a portal blocks or warns, that portal's agent stops and the user is notified; others continue.
- The workflow survives restarts (resumes from the last checkpoint, including pending approvals).

# HOW TO WORK
1. First, reply with: your understanding, any assumptions, open questions, and a phased build plan.
   Wait for my confirmation.
2. Then build in phases: (1) core state + profile + storage, (2) portal connector + search +
   matching, (3) HITL approval + application agent, (4) email monitor + reply drafting,
   (5) analytics, (6) tests + README.
3. After each phase, summarize what works, what's stubbed, and how to run it.