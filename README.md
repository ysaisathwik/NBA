# Intelligent Next Best Action (NBA) Platform

An **Agentic Decision Intelligence Platform** that turns customer interactions and
enterprise knowledge into explainable, human-reviewed **next best actions**.

This is a *reusable platform*, not a chatbot. A dynamic **Planner Agent** orchestrates a
constellation of specialist agents, each backed by purpose-specific tools, over a shared
multi-tier memory, with a **goal loop** that verifies resolution and replans until the goal
is reached.

> Reference business domain: **B2B Energy Operations** (transformer/grid asset health).
> The platform is domain-agnostic — swap the `domain/` pack to retarget SaaS Sales, Staffing,
> Customer Success, etc.

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

### The 13 agents
Planner · Context Retrieval · Intent Classification · Knowledge · Risk Assessment ·
Anomaly Detection · Recommendation · Explainability · Human-in-the-Loop · Execution ·
Verification · Memory Compression · Learning.

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
Trigger events, watch the live agent trace, and approve/modify/reject recommendations.

### 3) Run the tests
```bash
python -m pytest -q
```

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
