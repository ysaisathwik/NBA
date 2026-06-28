"""Simple JWT auth layer — production would use Supabase Auth. Hackathon-ready.

Self-contained HS256 JWT (no external deps) + an in-memory user registry that mirrors the
shape of a Supabase Auth + RBAC lookup. Also hosts the HITL approval hierarchy used to gate
who may approve which urgency tier.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

SECRET = os.getenv("JWT_SECRET", "nba-platform-dev-secret-change-in-prod")

# Hardcoded demo users — in prod, replace with Supabase Auth lookup.
# Company: Northwind Power. Emails follow firstname.lastname@northwindpower.com.
USERS = {
    "sarah.chen@northwindpower.com":   {"password": "Manager#2024",  "role": "manager",  "name": "Sarah Chen",   "id": "u-mgr-01"},
    "james.okafor@northwindpower.com": {"password": "Operator#2024", "role": "operator", "name": "James Okafor", "id": "u-opr-01"},
    "priya.sharma@northwindpower.com": {"password": "Engineer#2024", "role": "engineer", "name": "Priya Sharma", "id": "u-eng-01"},
    "alex.rivera@northwindpower.com":  {"password": "Customer#2024", "role": "customer", "name": "Alex Rivera",  "id": "u-cst-01"},
    "morgan.blake@northwindpower.com": {"password": "Admin#2024",    "role": "admin",    "name": "Morgan Blake", "id": "u-adm-01"},
}

ROLE_PERMISSIONS = {
    "admin":    {"can_approve": True,  "can_view_all": True,  "can_trigger": True,  "hitl_tier": 0, "auto_approve_limit": "P1"},
    "manager":  {"can_approve": True,  "can_view_all": True,  "can_trigger": True,  "hitl_tier": 1, "auto_approve_limit": "P2"},
    "operator": {"can_approve": True,  "can_view_all": False, "can_trigger": True,  "hitl_tier": 2, "auto_approve_limit": "P3"},
    "engineer": {"can_approve": False, "can_view_all": False, "can_trigger": False, "hitl_tier": 3, "auto_approve_limit": "P4"},
    "customer": {"can_approve": False, "can_view_all": False, "can_trigger": False, "hitl_tier": 4, "auto_approve_limit": None},
}

# For a given urgency tier, the MINIMUM role required to approve.
HITL_APPROVAL_MATRIX = {
    "P1": "manager",   # safety-critical → manager or admin only
    "P2": "manager",   # high-impact → manager minimum
    "P3": "operator",  # medium → operator can handle
    "P4": "operator",  # low → operator or auto-approve
}

# Only these urgency tiers are ever eligible for auto-approve (no human).
AUTO_APPROVE_ROLES = {"P3", "P4"}

# Lower rank = more authority. Used to compare a user's role to the required role.
ROLE_RANK = {"admin": 1, "manager": 2, "operator": 3, "engineer": 4, "customer": 5}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def create_token(email: str, ttl_hours: int = 8) -> str | None:
    user = USERS.get(email)
    if not user:
        return None
    payload = {"sub": user["id"], "email": email, "role": user["role"],
               "name": user["name"], "exp": int(time.time()) + ttl_hours * 3600}
    return _sign(payload)


def verify_token(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_body = f"{parts[0]}.{parts[1]}"
        expected_sig = _b64(hmac.new(SECRET.encode(), header_body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(parts[2], expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def login(email: str, password: str) -> dict | None:
    user = USERS.get(email)
    if not user or user["password"] != password:
        return None
    token = create_token(email)
    perms = ROLE_PERMISSIONS[user["role"]]
    return {"token": token, "role": user["role"], "name": user["name"],
            "email": email, "id": user["id"], "permissions": perms}


def get_permissions(role: str) -> dict:
    return ROLE_PERMISSIONS.get(role, {})


def can_approve_urgency(role: str, urgency: str) -> bool:
    """True if `role` has enough authority to approve a case of this urgency tier."""
    required = HITL_APPROVAL_MATRIX.get(urgency, "operator")
    return ROLE_RANK.get(role, 99) <= ROLE_RANK.get(required, 3)
