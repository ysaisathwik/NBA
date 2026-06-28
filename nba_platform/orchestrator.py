"""Goal-loop orchestrator — runs a case end-to-end across the agent constellation.

This is the LangGraph-equivalent state machine: Planner-driven lazy selection, parallel
analysis, HITL gating, transactional execution, verify→replan goal loop, then compression +
learning. It is deterministic and observable (every transition is logged + published).
"""
from __future__ import annotations

from typing import Any, Callable

from .runtime import Platform, Session
from .schemas import CaseState, Event, HumanReview

# urgency → (max replan iterations, first verification wait in virtual minutes)
_REPLAN_LIMITS = {"P1": 3, "P2": 2, "P3": 1, "P4": 0}
_VERIFY_WAIT = {"P1": 5, "P2": 15, "P3": 60, "P4": 240}

DecisionProvider = Callable[[Session, dict[str, Any]], dict[str, Any]]
FeedbackProvider = Callable[[Session], dict[str, Any]]


def default_reviewer(session: Session, package: dict[str, Any]) -> dict[str, Any]:
    """Simulated human reviewer used for offline demos/tests."""
    candidates = package.get("candidates", [])
    if not candidates:
        return HumanReview(decision="rejected", comments="no candidates", reviewer_id="operator-sim").model_dump()
    top = candidates[0]
    serves = (session.mem.get_context().get("asset_record") or {}).get("serves_customers")
    mods = ["Notify affected customers before load transfer"] if serves else []
    return HumanReview(
        decision="modified" if mods else "approved",
        selected_action=top["id"], modifications=mods,
        comments="Approved top recommendation." + (" Added customer notification." if mods else ""),
        reviewer_id="operator-sim", response_time_ms=94000,
    ).model_dump()


class Orchestrator:
    def __init__(self, platform: Platform, decision_provider: DecisionProvider | None = None,
                 feedback_provider: FeedbackProvider | None = None) -> None:
        self.platform = platform
        self.decision_provider = decision_provider or default_reviewer
        # Optional human feedback gate for the iterative resolution loop (API mode).
        self.feedback_provider = feedback_provider

    # ---------------------------------------------------------------- run
    def run(self, event: Event, goal: str | None = None) -> Session:
        # EC-06: concurrent events on the same asset are injected into the active session.
        active = getattr(self.platform, "active_assets", {})
        if event.asset_id and event.asset_id in active:
            existing: Session = active[event.asset_id]
            existing.log("Concurrent event injected", detail="merged into active session (EC-06)",
                         data=event.model_dump())
            existing.mem.publish({"type": "concurrent_event", "event": event.model_dump()})
            return existing

        session = self.platform.new_session(event, goal)
        return self.run_prepared(session)

    def run_prepared(self, session: Session) -> Session:
        """Run a pre-created session (used by the API so it can register the runner first)."""
        if not hasattr(self.platform, "active_assets"):
            self.platform.active_assets = {}
        asset_id = session.event.asset_id
        if asset_id:
            self.platform.active_assets[asset_id] = session
        try:
            self._run(session)
        finally:
            if asset_id and self.platform.active_assets.get(asset_id) is session:
                self.platform.active_assets.pop(asset_id, None)
        return session

    # ------------------------------------------------------------- stages
    def _run(self, session: Session) -> None:
        ev = session.event
        session.log("Event arrives", detail=f"{ev.type} · {ev.severity} · asset={ev.asset_id}", data=ev.model_dump())
        session.log("Ingestion & enrichment", detail="telemetry/context confirmed indexed")

        # 1) Context + Intent (critical pre-planning agents)
        ctx_res = self.platform.agent("context").run(session)
        session.set_state(CaseState.CONTEXT_LOADED)
        intent_res = self.platform.agent("intent").run(session)
        if intent_res.status == "failed":
            session.mem.set_state("force_human_review", True)

        # 2) Planner — lazy selection
        plan = self.platform.agent("planner").run(session)
        session.set_state(CaseState.PLAN_CREATED)

        # 3) Parallel analysis (knowledge / anomaly run before recommendation)
        session.set_state(CaseState.AGENTS_RUNNING)
        for name in plan.output.get("analysis_agents", []):
            if name == "risk":
                continue  # risk scores candidates → runs after recommendation
            res = self.platform.agent(name).run(session)
            if res.status in {"failed", "timeout"} and self.platform.agent(name).critical:
                session.mem.set_state("force_human_review", True)

        # EC: stale telemetry beyond freshness window → escalate, bypass recommendation loop
        if session.mem.get_state("telemetry_stale"):
            session.log("Telemetry stale", detail="data age exceeds freshness window — escalating to human")

        # 4) Recommendation → Risk → Explainability
        self.platform.agent("recommendation").run(session)
        if "risk" in plan.output.get("analysis_agents", []):
            self.platform.agent("risk").run(session)
        self.platform.agent("explainability").run(session)
        session.set_state(CaseState.RECOMMENDATION_READY)

        # 5) Human-in-the-Loop
        session.set_state(CaseState.HUMAN_REVIEW)
        hitl_res = self.platform.agent("hitl").run(session)
        review = session.mem.get_blob("human_review", {}) or {}
        if review.get("decision") == "pending":
            package = session.mem.get_blob("review_package", {})
            decision = self.decision_provider(session, package)
            session.mem.set_blob("human_review", decision)
            review = decision
            session.log("Human decision captured",
                        detail=f"{decision.get('decision')} · {decision.get('response_time_ms',0)//1000}s",
                        data={"modifications": decision.get("modifications")})

        if review.get("decision") == "rejected":
            session.set_state(CaseState.ESCALATED)
            session.log("Recommendation rejected", detail="case escalated for manual handling")
            self._compress_and_learn(session)
            return

        # 6) Generate the first dynamic follow-up question (interactive mode only).
        if self.feedback_provider and review.get("decision") in {"approved", "modified"} \
                and not review.get("auto_approved"):
            q = self.platform.agent("hitl").generate_followup(session, review)
            session.log("Follow-up question prepared", detail=q)

        # 7) Execution
        session.set_state(CaseState.EXECUTING)
        self.platform.agent("execution").run(session)

        # 8) Verify → replan goal loop
        self._goal_loop(session)

        # 9) Iterative human feedback loop (interactive mode only)
        self._interactive_feedback(session, review)

        # 10) Compression + Learning
        self._compress_and_learn(session)

    # --------------------------------------------------------- goal loop
    def _goal_loop(self, session: Session) -> None:
        intent = session.mem.get_state("intent", {}) or {}
        urgency = intent.get("urgency_tier", "P4")
        max_iter = _REPLAN_LIMITS.get(urgency, 0)
        wait = _VERIFY_WAIT.get(urgency, 240)
        iteration = 0

        while True:
            session.set_state(CaseState.VERIFYING)
            session.log("Verification wait", detail=f"advancing T+{wait}min (urgency {urgency})")
            session.advance_clock(wait)

            verify = self.platform.agent("verification").run(session).output
            if verify.get("resolved"):
                session.set_state(CaseState.RESOLVED)
                session.log("Resolution confirmed", detail=verify.get("reason"), data=verify.get("evidence"))
                return
            if verify.get("partial_resolve"):
                session.set_state(CaseState.PARTIAL_RESOLVE)
                session.log("Partial resolution", detail="technical fix confirmed; customer impact persists (EC-03)")
                return

            if iteration >= max_iter:
                session.set_state(CaseState.ESCALATED)
                session.log("Replan limit reached", detail=f"{urgency} exhausted {max_iter} iterations — escalating")
                return

            iteration += 1
            session.iteration = iteration
            session.mem.set_state("iteration", iteration)
            session.set_state(CaseState.REPLANNING)
            decision = self.platform.agent("planner").replan(session, verify)
            session.log(f"Goal loop — replan (iteration {iteration})",
                        detail=decision.get("reason"), data=decision)

            d = decision.get("decision")
            if d == "resolved_now":
                wait = 0
            elif d == "continue":
                wait = decision.get("next_wait_minutes", wait)
            elif d == "customer_followup":
                session.set_state(CaseState.PARTIAL_RESOLVE)
                return
            else:  # new_action — escalate to the next-best candidate and re-execute
                self._escalate_action(session)
                wait = _VERIFY_WAIT.get(urgency, 5)

    def _escalate_action(self, session: Session) -> None:
        candidates = session.mem.get_candidates()
        review = session.mem.get_blob("human_review", {}) or {}
        executed_id = review.get("selected_action")
        nxt = next((c for c in candidates if c["id"] != executed_id), None)
        if not nxt:
            return
        session.log("Escalating to alternative action", detail=nxt["action_type"])
        new_review = dict(review)
        new_review.update({"decision": "approved", "selected_action": nxt["id"],
                           "comments": "Goal loop: escalated to alternative action"})
        session.mem.set_blob("human_review", new_review)
        session.set_state(CaseState.EXECUTING)
        self.platform.agent("execution").run(session)

    # ------------------------------------------------- interactive feedback
    def _interactive_feedback(self, session: Session, review: dict[str, Any]) -> None:
        """Dynamic iterative confirmation loop driven by human yes/no feedback (API mode).

        Hooks in AFTER the goal loop. The operator confirms resolution or reports a persistent
        issue; each "No" triggers a replan + a more specific LLM follow-up question, bounded by
        the urgency replan limit.
        """
        if not self.feedback_provider or review.get("auto_approved"):
            return
        intent = session.mem.get_state("intent", {}) or {}
        urgency = intent.get("urgency_tier", "P4")
        max_loops = max(_REPLAN_LIMITS.get(urgency, 0), 2)

        if not session.mem.get_blob("followup_question"):
            self.platform.agent("hitl").generate_followup(session, review)

        loops = 0
        while True:
            session.set_state(CaseState.VERIFYING)
            session.log("Awaiting operator feedback", detail=session.mem.get_blob("followup_question"))
            fb = self.feedback_provider(session) or {}
            if fb.get("resolved"):
                session.set_state(CaseState.RESOLVED)
                session.log("Operator confirmed resolution", detail="case marked resolved by human feedback")
                return
            loops += 1
            if loops > max_loops:
                session.set_state(CaseState.ESCALATED)
                session.log("Feedback loop limit reached",
                            detail=f"escalating after {loops - 1} unresolved confirmations")
                return
            session.set_state(CaseState.REPLANNING)
            rp = self.platform.agent("planner").replan(session, {"resolved": False})
            session.log(f"Operator reports unresolved (iteration {loops})", detail=rp.get("reason"), data=rp)
            self._escalate_action(session)
            session.advance_clock(_VERIFY_WAIT.get(urgency, 5))
            self.platform.agent("verification").run(session)
            q = self.platform.agent("hitl").generate_specific_followup(session, review)
            session.log("Follow-up question", detail=q)

    # ----------------------------------------------------- post-resolution
    def _compress_and_learn(self, session: Session) -> None:
        # Capture the full case snapshot before Memory Compression flushes Redis.
        session.result = session.snapshot()
        session.set_state(CaseState.COMPRESSING)
        self.platform.agent("compression").run(session)
        self.platform.agent("learning").run(session)
        session.set_state(CaseState.CLOSED)
        session.log("Case closed", detail="memory compressed, learning updated, Redis flushed")
