"""Central configuration. Reads from environment (and .env if python-dotenv is present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Runtime settings for the platform."""

    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "").strip())
    llm_model: str = field(default_factory=lambda: os.environ.get("NBA_LLM_MODEL", "claude-opus-4-8"))
    llm_fast_model: str = field(default_factory=lambda: os.environ.get("NBA_LLM_FAST_MODEL", "claude-haiku-4-5"))
    domain: str = field(default_factory=lambda: os.environ.get("NBA_DOMAIN", "energy"))
    db_path: str = field(default_factory=lambda: os.environ.get("NBA_DB_PATH", "nba_platform.db"))
    force_offline: bool = field(default_factory=lambda: _flag("NBA_FORCE_OFFLINE"))

    # Supabase (optional). When both are set and the `supabase` package is installed, the
    # platform uses Supabase/Postgres as the long-term store; otherwise it falls back to SQLite.
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", "").strip())
    supabase_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_KEY", "").strip())
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", "nba-platform-dev-secret-change-in-prod"))

    # Email delivery (optional — falls back to log-only HTML files in emails/sent_log/).
    sendgrid_api_key: str = field(default_factory=lambda: os.environ.get("SENDGRID_API_KEY", "").strip())
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "").strip())
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", "").strip())
    smtp_pass: str = field(default_factory=lambda: os.environ.get("SMTP_PASS", "").strip())
    email_from: str = field(default_factory=lambda: os.environ.get("EMAIL_FROM", "noreply@nexus-platform.com"))
    # App base URL — used to build deep-link CTA URLs inside emails.
    app_base_url: str = field(default_factory=lambda: os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/"))

    # Tunables mirrored from the architecture reference.
    similarity_threshold: float = 0.25  # cold-start gate (local hashed embeddings run lower than 0.75)
    episodic_precedent_threshold: float = 0.80
    agent_timeout_s: float = 30.0
    embedding_dim: int = 256
    # Auto-approve gates. The reference uses confidence >= 0.95; local deterministic
    # confidences are lower-scale, so the default is calibrated to 0.85 (override via env if desired).
    auto_approve_confidence: float = 0.85
    auto_approve_risk: float = 0.30
    # Human-in-the-middle policy: customer-originated cases require a human reviewer and a
    # customer confirmation; never auto-closed. Flip to True only for fully autonomous demos.
    auto_approve_customer_cases: bool = field(default_factory=lambda: _flag("NBA_AUTO_APPROVE_CUSTOMER"))

    @property
    def llm_available(self) -> bool:
        """True when a real LLM can be used (key present and offline not forced)."""
        return bool(self.anthropic_api_key) and not self.force_offline

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Test helper to re-read environment."""
    global _settings
    _settings = None
