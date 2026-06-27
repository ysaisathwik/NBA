# NBA Platform — Architecture Walkthrough

This document explains the design and maps it to the reference specification. It is the
companion to the 5-minute architecture video.

## 1. Design philosophy (the three pillars)

1. **Lazy agent selection.** The Planner inspects intent + urgency + available context and
   activates the *minimum viable* agent subset. A routine billing query runs Context →
   Recommendation → Explainability → HITL; a P1 transformer alert fans out all 13 agents.
   See [`agents/planner.py`](../nba_platform/agents/planner.py) `_select_analysis`.
2. **Tool-or-LLM fallback.** Every agent first tries deterministic tools; only generative
   tasks call the LLM, and if the LLM is unavailable each agent has a rule-based path
   ([`llm.py`](../nba_platform/llm.py) returns `None` → fallback). This is also the
   *offline fallback mode* (EC-05) — the whole platform runs with zero API keys.
3. **Memory-first.** Before generating, agents consult episodic + semantic memory. A strong
   episodic precedent is injected into the Recommendation Agent and boosts the matching action.

## 2. Component map

```
runtime.Platform ── working memory (Redis-like)      memory/working.py
                 ├─ long-term store (Supabase-like)   memory/longterm.py   (SQLite + vector)
                 ├─ tool registry                     tools/ (base + catalog + simulated)
                 ├─ domain pack                       domain/energy/
                 ├─ llm client (+offline fallback)    llm.py
                 └─ 13 agents                          agents/

orchestrator.Orchestrator  ── the goal-loop state machine that drives a Session
runtime.Session            ── one case: blackboard + virtual clock + observable trace
api/app.py                 ── FastAPI command-centre + HITL review queue + web UI
```

## 3. Memory architecture (4 tiers)

| Tier | Backed by | Holds | Lifecycle |
|---|---|---|---|
| **Working / short-term** | `WorkingMemory` (dict + TTL + pub/sub) | session state, agent outputs, context, candidates, risk, review, exec, verify | flushed on resolution |
| **Episodic** | SQLite `episodic_memory` | past case summaries keyed by event fingerprint | permanent, retrieved first |
| **Semantic** | SQLite `embeddings` + cosine | chunked SOPs/manuals/history/case-summaries | permanent, grows via ingestion + compression |
| **Learning** | SQLite `learning_patterns` | pattern→action success rates | updated after every case |

Keys are namespaced `session:{sid}:*` exactly as in the reference; pub/sub drives the live
trace. The two tiers are kept strictly separate (the reference's most-common mistake).

## 4. Agent catalogue

| Agent | Mode | Tools (representative) | Output |
|---|---|---|---|
| Planner | LLM + rule templates | session state, agent registry, plan templates | execution plan |
| Context | tool (LLM re-rank) | vector_search, episodic_lookup, asset/customer/tickets | context package, cold-start flag |
| Intent | LLM-primary + keyword fallback | fast LLM, keyword classifier, domain rules | intent + urgency + impact |
| Knowledge | tool-primary | sop_search (broadens on cold start) | SOP/manual/reg chunks |
| Anomaly | **tool-only** (deterministic) | scada_telemetry, telemetry_stats, weather | z-score, ETA, root cause |
| Risk | tool-primary (deterministic rules) | domain risk rules, erp_cost | 5-dim risk vector per action |
| Recommendation | LLM + template fallback | templates, episodic precedent, learning, feasibility | 3–5 ranked actions |
| Explainability | LLM + template | citation validator, confidence calculator | rationale + citations + what-if |
| HITL | internal | auto-approve engine, audit, routing | decision / pending + escalation plan |
| Execution | tool (saga) | work-order, dispatch, notify, automation, crm, asset update | transactional exec result |
| Verification | **tool-only** | scada poll, crm sentiment, survey | resolved / partial / not |
| Compression | LLM + template | summariser, chunker, embedder, episodic writer, flush | episodic case + vectors |
| Learning | internal (online update) | pattern upserter, audit | updated success rates |

## 5. End-to-end workflow & goal loop

`Orchestrator._run` realises the 15-step trace: Event → Ingestion → Context → Intent →
Planner → parallel analysis (Knowledge/Anomaly) → Recommendation → Risk → Explainability →
HITL → Execution → **verify→replan goal loop** → Compression → Learning.

The goal loop (`_goal_loop`) is the differentiator: after execution it waits a virtual
verification window (P1 5min … P4 4h), re-checks telemetry/ticket/customer/KPI, and either
confirms RESOLVED, flags PARTIAL_RESOLVE (EC-03), or REPLANs. Replan limits: P1=3, P2=2,
P3=1, P4=0 — then ESCALATED. The demo replays exactly: 142°C → (T+5) 138°C not resolved →
replan "trajectory acceptable, continue" → (T+45) 94°C RESOLVED.

State machine: `INITIATED → CONTEXT_LOADED → PLAN_CREATED → AGENTS_RUNNING →
RECOMMENDATION_READY → HUMAN_REVIEW → EXECUTING → VERIFYING → (REPLANNING ↺) →
RESOLVED/PARTIAL_RESOLVE/ESCALATED → COMPRESSING → CLOSED` (see `schemas.CaseState`).

## 6. Edge cases handled

| Case | Where |
|---|---|
| EC-01 conflicting sensor/human data | HITL escalation + human>sensor precedence (aggregation rules) |
| EC-02 cold start | `context.py` (weak semantic + no episodic) → caps confidence, blocks auto-approve, broadens knowledge query |
| EC-03 partial resolution | `verification.py` partial flag → goal loop `PARTIAL_RESOLVE` |
| EC-04 agent timeout / failure | `agents/base.py` never crashes; `orchestrator` forces human review when a *critical* agent fails |
| EC-05 LLM unavailability | `llm.py` retries+backoff then `None` → deterministic fallback everywhere |
| EC-06 concurrent events on an asset | `orchestrator.run` merges into the active session |
| EC-07 reviewer timeout | API `SessionRunner.provider` emergency override after SLA |
| EC-08 data quality | low-similarity / stale telemetry flags lower confidence and force review |

Plus: **idempotent execution** (per session+action key), **saga compensation** on partial
execution failure, and **deterministic risk floors** (live HV → safety-critical).

## 7. Success metrics

`/api/metrics` and the audit log expose: episodic hit rate, learning sample growth,
auto-approve rate, goal-loop iterations, and per-agent latency/confidence (in each trace
entry). These map to the reference's operational / quality / reliability / business metrics.

## 8. Reusability & extensibility

- **New agent:** subclass `agents.base.Agent`, register in `agents/__init__.AGENT_CLASSES`,
  add it to a planner selection rule. Nothing else changes.
- **New tool:** add to `tools/catalog.build_registry`; agents resolve it by name.
- **New domain:** add `domain/<name>/config.py` + `seed.py`, set `NBA_DOMAIN`. The agent
  framework, memory, goal loop, tools, and deployment transfer unchanged.

## 9. Deviations from the reference (and why)

Built to run anywhere with no cloud infra, the implementation substitutes:

| Reference | Here | Rationale |
|---|---|---|
| Redis | in-process `WorkingMemory` (same API surface) | zero-setup; swap for `redis-py` unchanged |
| Supabase + pgvector | SQLite + Python cosine | zero-setup; same query shapes |
| OpenAI embeddings | local hashed BoW embeddings | offline; pluggable via `embeddings.embed` |
| Claude/GPT (required) | Claude optional, deterministic fallback | demo always works; set `ANTHROPIC_API_KEY` to enable |
| Kafka/LangGraph | direct orchestrator + pub/sub | simpler, deterministic, observable |
| Auto-approve conf ≥ 0.95 | ≥ 0.85 (configurable) | local deterministic confidences are lower-scale |

All architectural concepts — planner orchestration, agent/tool reuse, four memory tiers,
goal loop, HITL, learning — are implemented in full.
