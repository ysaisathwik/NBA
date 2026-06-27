"""End-to-end demo: replays the transformer TR-441 P1 scenario through all 13 agents.

Runs fully offline (deterministic). Prints the live agent trace, the recommendations with
explanations, the human decision, the goal-loop replan, and the memory/learning update.
"""
from __future__ import annotations

import sys

try:  # ensure unicode renders on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from nba_platform.config import Settings
from nba_platform.orchestrator import Orchestrator
from nba_platform.runtime import Platform
from nba_platform.schemas import Event

C = {
    "h": "\033[95m", "b": "\033[94m", "g": "\033[92m", "y": "\033[93m",
    "r": "\033[91m", "d": "\033[2m", "x": "\033[0m", "bold": "\033[1m",
}


def banner(text: str) -> None:
    print(f"\n{C['h']}{C['bold']}{'=' * 78}\n{text}\n{'=' * 78}{C['x']}")


def print_trace(session) -> None:
    for s in session.trace:
        tag = s.get("agent") or ""
        color = C["g"] if "Resolution" in s["title"] or s["state"] == "RESOLVED" else C["b"]
        print(f"{color}[{s['step']:>2}] {s['state']:<18}{C['x']} {C['bold']}{s['title']}{C['x']}"
              + (f" {C['d']}— {s['detail']}{C['x']}" if s.get("detail") else ""))


def print_recommendations(session) -> None:
    result = session.result or {}
    candidates = result.get("candidates", [])
    explanations = {e["action_id"]: e for e in result.get("explanations", []) or []}
    if not candidates:
        return
    banner("RANKED NEXT BEST ACTIONS (with explanations)")
    for i, c in enumerate(candidates, 1):
        exp = explanations.get(c["id"], {})
        risk = c.get("risk", {})
        print(f"{C['bold']}{i}. {c['action_type']}{C['x']}  "
              f"priority={c['priority_score']:.2f} confidence={c['confidence']:.2f} "
              f"impact={c['estimated_impact']} eta={c['estimated_duration']}")
        print(f"   {c['description']}")
        if exp.get("rationale"):
            print(f"   {C['d']}rationale:{C['x']} {exp['rationale']}")
        if risk:
            print(f"   {C['y']}risk:{C['x']} safety={risk.get('safety')} financial={risk.get('financial')} "
                  f"compliance={risk.get('compliance')} reputational={risk.get('reputational')}")
        if c.get("precedent_case"):
            print(f"   {C['d']}precedent:{C['x']} {c['precedent_case']}")
        print()


def run_case(platform: Platform, event: Event, title: str):
    banner(title)
    orch = Orchestrator(platform)
    session = orch.run(event)
    print_trace(session)
    print_recommendations(session)
    result = session.result or {}
    review = result.get("human_review", {}) or {}
    verify = result.get("verify", {}) or {}
    print(f"{C['bold']}Human decision:{C['x']} {review.get('decision')} "
          f"(auto_approved={review.get('auto_approved')}) — {review.get('comments','')}")
    print(f"{C['bold']}Final state:{C['x']} {session.state.value} | iterations={session.iteration} "
          f"| resolved={verify.get('resolved')} partial={verify.get('partial_resolve')}")
    return session


def main() -> None:
    settings = Settings(db_path=":memory:")
    platform = Platform(settings=settings)
    print(f"{C['d']}LLM mode: {'Claude (live)' if platform.llm.available else 'deterministic offline fallback'}{C['x']}")

    # --- Case 1: the flagship P1 transformer overheating scenario --------
    event = Event(
        type="sensor_alert", source_type="scada", asset_id="TR-441", customer_id="CUST-Northgate",
        metric="temperature", value=142, threshold=110, severity="CRITICAL",
        raw_content="Transformer TR-441 temperature 142C exceeds 110C threshold; thermal overload risk.",
        metadata={"duration_minutes": 47},
    )
    run_case(platform, event, "CASE 1 — P1 Transformer TR-441 overheating (all 13 agents, goal loop)")

    # show learning moved the success rate
    pat = platform.store.get_pattern("equipment_fault|transformer|CRITICAL")
    if pat:
        print(f"\n{C['g']}Learning:{C['x']} pattern '{pat['pattern_hash']}' "
              f"success_rate now {pat['success_rate']} over {pat['sample_count']} samples")

    # --- Case 2: a low-stakes billing query → lazy selection + auto-approve
    billing = Event(
        type="billing_dispute", source_type="crm", customer_id="CUST-Northgate", severity="INFO",
        raw_content="Customer disputes an invoice charge and requests a refund review.",
    )
    run_case(platform, billing, "CASE 2 — Low-stakes billing dispute (lazy agent selection)")

    # --- Case 3: cold start (novel asset + signal, no history) -----------
    cold = Event(
        type="data_anomaly", source_type="scada", asset_id="WT-19", metric="blade_harmonic",
        value=8.2, threshold=5.0, severity="WARNING",
        raw_content="Anomalous blade vibration harmonic signature on wind turbine WT-19; no prior records.",
    )
    run_case(platform, cold, "CASE 3 — Cold start handling (EC-02: novel signal)")

    banner("PLATFORM SUMMARY")
    print(f"episodic cases stored : {platform.store.count('episodic_memory')}")
    print(f"vector chunks indexed : {platform.store.count('embeddings')}")
    print(f"audit log entries     : {platform.store.count('audit_log')}")
    print(f"learning patterns     : {platform.store.count('learning_patterns')}")


if __name__ == "__main__":
    main()
