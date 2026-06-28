"""Agent catalogue. Each agent is reusable, independently testable, and resolved by name."""
from __future__ import annotations

from .base import Agent, AgentResult
from .dialogue_parser import DialogueParserAgent
from .planner import PlannerAgent
from .context import ContextAgent
from .intent import IntentAgent
from .knowledge import KnowledgeAgent
from .risk import RiskAgent
from .anomaly import AnomalyAgent
from .recommendation import RecommendationAgent
from .explainability import ExplainabilityAgent
from .hitl import HITLAgent
from .execution import ExecutionAgent
from .verification import VerificationAgent
from .compression import MemoryCompressionAgent
from .learning import LearningAgent

AGENT_CLASSES = {
    "dialogue_parser": DialogueParserAgent,
    "planner": PlannerAgent,
    "context": ContextAgent,
    "intent": IntentAgent,
    "knowledge": KnowledgeAgent,
    "risk": RiskAgent,
    "anomaly": AnomalyAgent,
    "recommendation": RecommendationAgent,
    "explainability": ExplainabilityAgent,
    "hitl": HITLAgent,
    "execution": ExecutionAgent,
    "verification": VerificationAgent,
    "compression": MemoryCompressionAgent,
    "learning": LearningAgent,
}


def build_agents(platform) -> dict[str, Agent]:
    return {name: cls(platform) for name, cls in AGENT_CLASSES.items()}


__all__ = ["Agent", "AgentResult", "AGENT_CLASSES", "build_agents"]
