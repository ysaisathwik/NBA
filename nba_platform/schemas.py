"""Shared data contracts for events, agent I/O, and decisions.

Pydantic models give us validation + JSON (de)serialisation for free, which mirrors the
structured-output discipline described in the architecture reference.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Urgency(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseState(str, Enum):
    INITIATED = "INITIATED"
    CONTEXT_LOADED = "CONTEXT_LOADED"
    PLAN_CREATED = "PLAN_CREATED"
    AGENTS_RUNNING = "AGENTS_RUNNING"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REPLANNING = "REPLANNING"
    RESOLVED = "RESOLVED"
    PARTIAL_RESOLVE = "PARTIAL_RESOLVE"
    ESCALATED = "ESCALATED"
    COMPRESSING = "COMPRESSING"
    CLOSED = "CLOSED"


class Event(BaseModel):
    """A normalised inbound signal (SCADA alert, CRM update, email, ticket, ...)."""

    id: str = Field(default_factory=lambda: new_id("evt"))
    type: str = "generic_event"  # e.g. sensor_alert, billing_dispute, customer_complaint
    source_type: str = "manual"  # scada, crm, email, ticket, billing, document
    asset_id: str | None = None
    customer_id: str | None = None
    metric: str | None = None
    value: float | None = None
    threshold: float | None = None
    severity: str = "INFO"  # INFO / WARNING / MAJOR / CRITICAL
    raw_content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow)

    def fingerprint(self) -> str:
        """SHA256 over the discriminating fields — used for episodic lookup."""
        basis = f"{self.type}:{self.asset_id or ''}:{self.metric or ''}:{self.severity}"
        return hashlib.sha256(basis.encode()).hexdigest()


class Intent(BaseModel):
    primary_intent: str = "routine_inquiry"
    urgency_tier: Urgency = Urgency.P4
    domains: list[str] = Field(default_factory=list)
    customer_impact_score: float = 0.0
    confidence: float = 0.0
    method: str = "llm"  # llm / verified / keyword_fallback / rules


class AnomalyReport(BaseModel):
    detected: bool = False
    anomaly_type: str = "none"
    severity: str = "INFO"
    current_value: float | None = None
    threshold: float | None = None
    z_score: float | None = None
    duration_minutes: int | None = None
    predicted_failure_eta_minutes: int | None = None
    root_cause_hypothesis: str | None = None
    similar_historical_cases: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    stale: bool = False


class RiskVector(BaseModel):
    financial: float = 0.0
    safety: float = 0.0
    compliance: float = 0.0
    reputational: float = 0.0
    operational: float = 0.0

    def aggregate(self) -> float:
        return max(self.financial, self.safety, self.compliance, self.reputational, self.operational)

    def level(self) -> RiskLevel:
        a = self.aggregate()
        if a >= 0.85:
            return RiskLevel.CRITICAL
        if a >= 0.6:
            return RiskLevel.HIGH
        if a >= 0.3:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def all_below(self, threshold: float) -> bool:
        return all(
            v <= threshold
            for v in (self.financial, self.safety, self.compliance, self.reputational, self.operational)
        )


class Candidate(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    action_type: str
    description: str
    priority_score: float = 0.0
    confidence: float = 0.0
    estimated_impact: str = "MEDIUM"
    estimated_duration: str = "unknown"
    prerequisites: list[str] = Field(default_factory=list)
    resource_requirements: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    template_derived: bool = False
    precedent_case: str | None = None
    risk: RiskVector | None = None
    feasible: bool = True
    sla_ok: bool = True


class Explanation(BaseModel):
    action_id: str
    rationale: str
    citations: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    uncertainty: float = 0.06
    risk_summary: str = ""
    what_if_not_acted: str = ""
    auto_generated: bool = False
    disclaimer: str | None = None


class HumanReview(BaseModel):
    decision: str = "pending"  # approved / modified / rejected / pending
    selected_action: str | None = None
    modifications: list[str] = Field(default_factory=list)
    comments: str = ""
    reviewer_id: str | None = None
    response_time_ms: int = 0
    auto_approved: bool = False
    emergency_override: bool = False


class ExecResult(BaseModel):
    success: bool = False
    work_order_id: str | None = None
    crm_update_id: str | None = None
    crew_assignment_id: str | None = None
    notifications_sent: list[str] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    rollback_available: bool = True
    rolled_back: bool = False
    execution_timestamp: str = Field(default_factory=utcnow)


class VerifyResult(BaseModel):
    resolved: bool = False
    partial_resolve: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
