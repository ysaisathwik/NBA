"""Deterministic simulators standing in for external enterprise systems.

These mimic the REST/MQTT endpoints in the reference (SCADA, ERP, CRM, Field Ops, Comms,
Automation, Weather) so the platform runs end-to-end with no external dependencies. Every
output is a pure function of inputs → reproducible demos and tests. Replace any class with a
real HTTP client and the agents/tools above are unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from ..schemas import new_id


# --------------------------------------------------------------------------- #
# SCADA / telemetry
# --------------------------------------------------------------------------- #
def simulate_metric(baseline: float, normal: float, elapsed_min: float, interventions: list[dict]) -> float:
    """Model a degrading sensor that responds to interventions over (virtual) time."""
    value = baseline
    for iv in interventions:
        t = elapsed_min - iv.get("at", 0)
        if t < 0:
            continue
        if iv.get("kind") == "ramp":
            value = min(value, baseline - iv.get("rate", 0.5) * t)
        elif iv.get("kind") == "step":
            if t >= iv.get("delay", 0):
                value = min(value, normal)
    return round(max(value, normal), 1)


class SCADASimulator:
    def read(self, sim: dict[str, Any]) -> dict[str, Any]:
        baseline = sim.get("baseline", 0.0)
        normal = sim.get("normal", baseline)
        elapsed = sim.get("elapsed_min", 0.0)
        interventions = sim.get("interventions", [])
        value = simulate_metric(baseline, normal, elapsed, interventions)
        return {
            "asset_id": sim.get("asset_id"),
            "metric": sim.get("metric", "value"),
            "value": value,
            "threshold": sim.get("threshold"),
            "elapsed_min": elapsed,
            "stale": sim.get("stale", False),
        }

    def history_stats(self, sim: dict[str, Any]) -> dict[str, Any]:
        # synthetic 90-day baseline for z-score: mean near normal, modest std
        normal = sim.get("normal", sim.get("baseline", 0.0))
        return {"mean": normal, "std": max(1.0, abs(normal) * 0.06)}


# --------------------------------------------------------------------------- #
# ERP / Field Ops / CRM / Comms / Automation / Weather
# --------------------------------------------------------------------------- #
class ERPSimulator:
    cost_table = {
        "emergency_fan_replacement": 180_000,
        "load_reduction_coolant": 22_000,
        "isolation_reroute": 65_000,
        "cooling_boost_monitor": 3_500,
        "schedule_maintenance": 8_000,
        "billing_adjustment": 1_200,
        "dispatch_inspection": 4_800,
    }

    def cost_estimate(self, action_type: str) -> dict[str, Any]:
        return {"action_type": action_type, "estimated_cost_usd": self.cost_table.get(action_type, 5_000)}

    def resource_availability(self, action_type: str) -> dict[str, Any]:
        # crews/parts generally available; isolation needs a switching window
        available = action_type not in {"isolation_reroute"} or True
        return {"crew_available": True, "parts_in_stock": True, "available": available}

    def create_work_order(self, action_type: str, asset_id: str | None) -> dict[str, Any]:
        return {"work_order_id": new_id("WO"), "action_type": action_type, "asset_id": asset_id, "eta_min": 45}


class FieldOpsSimulator:
    def dispatch(self, work_order_id: str) -> dict[str, Any]:
        return {"crew_assignment_id": new_id("crew"), "work_order_id": work_order_id, "routed": True}


class CRMSimulator:
    def update_ticket(self, ticket_id: str, status: str) -> dict[str, Any]:
        return {"crm_update_id": new_id("crm"), "ticket_id": ticket_id, "status": status}

    def sentiment(self, base_sentiment: float, elapsed_min: float) -> float:
        # sentiment recovers slowly once action is underway
        return round(min(1.0, base_sentiment + 0.02 * min(elapsed_min, 15)), 2)


class CommsSimulator:
    def notify(self, audience: str, count: int) -> dict[str, Any]:
        return {"notification_id": new_id("ntf"), "audience": audience, "recipients": count, "sent": True}

    def survey(self, customer_id: str | None) -> dict[str, Any]:
        return {"survey_id": new_id("svy"), "customer_id": customer_id, "positive_pct": 73}


class AutomationSimulator:
    def trigger(self, workflow: str) -> dict[str, Any]:
        return {"automation_id": new_id("auto"), "workflow": workflow, "triggered": True}


class WeatherSimulator:
    def current(self, lat: float | None, lon: float | None) -> dict[str, Any]:
        # deterministic pseudo-weather from coordinates
        seed = (abs(lat or 0) + abs(lon or 0))
        temp_c = round(18 + 12 * math.sin(seed), 1)
        return {"temp_c": temp_c, "humidity": 0.55, "storm": temp_c > 28, "heatwave": temp_c > 28}


class Simulators:
    """Bundle of all simulated systems, shared across a platform instance."""

    def __init__(self) -> None:
        self.scada = SCADASimulator()
        self.erp = ERPSimulator()
        self.fieldops = FieldOpsSimulator()
        self.crm = CRMSimulator()
        self.comms = CommsSimulator()
        self.automation = AutomationSimulator()
        self.weather = WeatherSimulator()
