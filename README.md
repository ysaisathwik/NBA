# NexusAgent — Intelligent Next Best Action Platform

*Built by **Team HMS**.*

An **Agentic Decision Intelligence Platform** that turns customer interactions and
enterprise knowledge into explainable, human-reviewed **next best actions**.

This is a *reusable platform*, not a chatbot. A dynamic **Planner Agent** orchestrates a
constellation of specialist agents, each backed by purpose-specific tools, over a shared
multi-tier memory, with a **goal loop** that verifies resolution and replans until the goal
is reached.

Every case runs on **commands and confirmations** — agents are analysts, humans decide.
A case only closes after passing **three human checkpoints**:
**Gate 1 — Authorise** (manager/operator), **Gate 2 — Work done** (engineer or operator),
**Gate 3 — Customer confirmation**. Nothing auto-closes silently.

> Reference business domain: **B2B Energy Operations** (transformer/grid asset health),
> demoed as the fictional utility **Northwind Power**. The platform is domain-agnostic —
> swap the `domain/` pack to retarget SaaS Sales, Staffing, Customer Success, etc.

The web UI is a warm, cream-themed role workspace (Fraunces + Inter typography) with a live
`Gate 1 → Gate 2 → Gate 3` status bar, animated agent trace, risk heat-bars, and a
customer-facing progress timeline.

Each gate also fires a **rich HTML email** (cream/navy transactional design) to the right
person with a deep-link CTA back to the correct dashboard — case-opened, approval-required,
task-assigned, engineer-dispatched, and work-done-please-confirm. With no mail provider
configured, emails are rendered to `nba_platform/emails/sent_log/*.html` for local preview,
and live samples are available at `GET /api/emails/preview/{type}`.

---

## Why this is a platform, not a RAG bot

| Capability | Where it lives |
|---|---|
| Dynamic planner-based orchestration | [`nba_platform/agents/planner.py`](nba_platform/agents/planner.py) + [`orchestrator.py`](nba_platform/orchestrator.py) |
| Reusable agent + tool architecture | [`nba_platform/agents/base.py`](nba_platform/agents/base.py), [`nba_platform/tools/base.py`](nba_platform/tools/base.py) |
| Shared memory (4 tiers) | [`nba_platform/memory/`](nba_platform/memory/) |
| Retrieval/reasoning across sources | Context + Knowledge agents + vector store |
| Explainable recommendations w/ evidence | [`agents/explainability.py`](nba_platform/agents/explainability.py) |
| Configurable workflows + business rules | [`domain/energy/config.py`](nba_platform/domain/energy/config.py) |
| Extensible framework | drop a new agent in `agents/`, register a tool, add a domain pack |
| Human-in-the-Loop review | [`agents/hitl.py`](nba_platform/agents/hitl.py) + Web UI review queue |
| Memory-driven learning | [`agents/compression.py`](nba_platform/agents/compression.py), [`agents/learning.py`](nba_platform/agents/learning.py) |

### The agents
**Dialogue Parser** (free-text → structured event + keyword→agent matching) · Planner ·
Context Retrieval · Intent Classification · Knowledge · Risk Assessment · Anomaly Detection ·
Recommendation · Explainability · Human-in-the-Loop (with dynamic iterative follow-up) ·
Execution · Verification · Memory Compression · Learning.

### Memory tiers
- **Working (Redis-like)** — session blackboard, TTL, pub/sub. `memory/working.py`
- **Episodic (long-term)** — past cases by event fingerprint. `memory/longterm.py`
- **Semantic (vector)** — chunked manuals/SOPs/history. `memory/vector.py`
- **Learning** — pattern→outcome success rates. `memory/longterm.py`

---

## Quick start

```bash
cd Hackathon
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# bash:                source .venv/bin/activate
pip install -r requirements.txt
```

### 1) Run the end-to-end demo (offline, deterministic — no API keys needed)
```bash
python run_demo.py
```
This replays the **transformer TR-441 overheating** P1 scenario through all 13 agents,
including the goal loop / replan and memory compression — printing the 15-step trace.

### 2) Run the web command-centre + HITL review queue
```bash
python -m nba_platform.api.app
# open http://127.0.0.1:8000
```
- **Describe a problem in plain English** in the dialogue box at the top — the
  `DialogueParserAgent` extracts a structured `Event` and shows which specialist agents the
  keywords matched (coloured badges) before the pipeline runs.
- Watch the live agent trace, then **approve / modify / reject** recommendations.
- After your decision, the platform asks a **dynamic LLM follow-up question** ("Has the
  transformer returned below 110°C?"). Answer **Yes, resolved** to close, or **No, still an
  issue** to trigger a replan and a more specific next question — an iterative resolution loop.

Key endpoints: `POST /api/dialogue` (free-text → event + matched agents),
`POST /api/events` (structured event), `POST /api/sessions/{sid}/decision` (HITL decision),
`POST /api/sessions/{sid}/feedback` (`{resolved: true|false}` iterative loop).

### Sign in with a role (multi-role dashboards)
The UI opens on a **login screen**. Pick a demo account to see its dashboard:

| Name | Email | Password | Role | Dashboard |
|---|---|---|---|---|
| Sai Sathwik | 121103ysaisathwik@gmail.com | `Manager#2024` | manager | KPIs, all cases, **approval queue (P1/P2)**, analytics |
| Harshitha P. | harshithapembarthi953@gmail.com | `Operator#2024` | operator | dialogue box, my cases, **HITL queue (P3/P4)** |
| Priya Sharma | priya.sharma@northwindpower.com | `Engineer#2024` | engineer | assigned work orders, asset health (read-only) |
| Tejo Murtula | mtejomurtula@gmail.com | `Customer#2024` | customer | my service cases + status tracker, file a complaint |
| Morgan Blake | morgan.blake@northwindpower.com | `Admin#2024` | admin | everything + audit |

**Guardrails:** nonsense / "No problem" / out-of-domain input is rejected (422) with a helpful
message — no session starts. **Tiered approval:** P1/P2 require **manager+**; an operator
approving a P1 gets a 403. **Auto-approve** only fires for P3/P4, low-risk, safety≈0,
whitelisted actions. **Relevance gate:** weakly-grounded cases get a "gather more information"
recommendation and forced human review; confidence is scaled by a relevance score (shown as a
bar in the UI). Every agent step shows **latency + estimated cost**; confidence **decays 15%**
per replan iteration.

Auth is JWT (HS256) — see [`nba_platform/auth.py`](nba_platform/auth.py). Set `JWT_SECRET` in
`.env` for production.

### Using Supabase (optional)
The long-term store defaults to SQLite. To use **Supabase / Postgres**:
1. `pip install supabase`
2. Run [`supabase_schema.sql`](supabase_schema.sql) in the Supabase SQL editor.
3. Set `SUPABASE_URL` and `SUPABASE_KEY` in `.env`.

The platform auto-detects both vars + the package and switches the store
([`nba_platform/memory/factory.py`](nba_platform/memory/factory.py)); if anything is missing
or unreachable it logs a warning and falls back to SQLite. `GET /api/metrics` reports the
active `store_backend`.

### 3) Run the tests
```bash
python -m pytest -q
```

---

## Docker deployment

### With Docker Compose (recommended)
```bash
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY and JWT_SECRET
docker compose up --build
```
Open http://localhost:8000

Demo credentials:
| Role | Email | Password |
|---|---|---|
| Manager | 121103ysaisathwik@gmail.com | `Manager#2024` |
| Operator | harshithapembarthi953@gmail.com | `Operator#2024` |
| Engineer | priya.sharma@northwindpower.com | `Engineer#2024` |
| Customer | mtejomurtula@gmail.com | `Customer#2024` |
| Admin | morgan.blake@northwindpower.com | `Admin#2024` |

### Offline demo (no API key)
```bash
NBA_FORCE_OFFLINE=1 docker compose up --build
```

### Health check
```bash
curl http://localhost:8000/health
# {"status":"ok","llm":"offline","store":"sqlite","sessions_active":0}
```

### Environment variables
See `.env.example` for all options.

---

## The command & confirmation model

Every case passes **three human checkpoints** — agents are analysts, humans decide:

1. **Gate 1 — Authorise**: a manager/operator authorises the recommended action (P1/P2 need a
   manager). Customer-raised issues are never auto-closed.
2. **Gate 2 — Work done**: the assigned worker confirms completion — an **engineer** for field
   work, an **operator** for billing/processing tasks. "Blocked" escalates the case.
3. **Gate 3 — Customer**: the **customer** confirms the issue is resolved (plain-English
   question + star rating). Only then does the case close and the learning cycle run.

The UI shows this as a live `Gate 1 → Gate 2 → Gate 3` status bar.

---

## Rich HTML email notifications

Each gate automatically emails the right person a cream/navy transactional HTML email with a
deep-link CTA button back to the correct dashboard:

| Email | Recipient | When |
|---|---|---|
| Case opened | customer | on submission |
| Approval required | manager / operator | Gate 1 (authorisation needed) |
| Task assigned | engineer or operator | Gate 2 (work to do) |
| Engineer dispatched | customer | after execution (field work) |
| Work done — please confirm | customer | Gate 2 complete → awaiting Gate 3 |

Delivery order: **SendGrid → SMTP → log-only**. With no provider configured, every email is
rendered to `nba_platform/emails/sent_log/*.html` (open in a browser to preview). Configure a
provider in `.env`:

```bash
SENDGRID_API_KEY=          # option A
SMTP_HOST= / SMTP_PORT= / SMTP_USER= / SMTP_PASS=   # option B
EMAIL_FROM=noreply@nexus-platform.com
APP_BASE_URL=http://localhost:8000   # used to build the deep-link CTAs
```

Preview any template in the browser (no auth):
`GET /api/emails/preview/{case_opened|engineer_assigned|work_done|approval_required|task_engineer|task_operator}`.
SendGrid is optional (`pip install sendgrid`); the import is guarded.

Code: [`nba_platform/emails/templates.py`](nba_platform/emails/templates.py) (HTML builders),
[`nba_platform/emails/sender.py`](nba_platform/emails/sender.py) (delivery),
[`nba_platform/agents/notification.py`](nba_platform/agents/notification.py) (dispatch).

---

## Using real LLMs (optional)

Everything runs offline using deterministic tool-mode + rule-based generation (this is the
spec's *offline fallback mode*, EC-05). To enable **Claude** for the generative agents
(Recommendation, Explainability, Planner), set:

```bash
# .env  (copy from .env.example)
ANTHROPIC_API_KEY=sk-ant-...
NBA_LLM_MODEL=claude-opus-4-8          # reasoning
NBA_LLM_FAST_MODEL=claude-haiku-4-5    # classification / summaries
```
The platform auto-detects the key; with no key it transparently uses deterministic fallbacks
so the demo always works.

---

## Architecture at a glance

```
  Event ──▶ Ingestion ──▶ Planner (lazy agent selection)
                              │  publishes tasks on Redis-like pub/sub
        ┌─────────────────────┼─────────────────────────────┐
     Context   Intent   Knowledge  Anomaly  Risk   (parallel)
        └─────────────────────┼─────────────────────────────┘
                          Recommendation ─▶ Explainability ─▶ HITL
                                                               │ approve/modify
                                                          Execution (saga)
                                                               │
                                                          Verification ──┐
                                                          resolved? ─ no ─┘ replan (goal loop)
                                                               │ yes
                                                  Memory Compression ─▶ Learning
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design walkthrough and the
mapping to the reference document.

## Retargeting to a new domain
1. Add `nba_platform/domain/<your_domain>/config.py` (intent taxonomy, action templates, risk rules).
2. Add `seed.py` (assets/customers/knowledge/episodic seed).
3. Point `NBA_DOMAIN=<your_domain>`.
The agent framework, memory, goal loop, and tools transfer unchanged.
