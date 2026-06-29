"""Email delivery layer.

Priority order:
1. SendGrid (if SENDGRID_API_KEY set)
2. SMTP (if SMTP_HOST set)
3. Log-only fallback (always works — logs to console and saves the HTML to a file)

In all cases the rendered HTML is stored in nba_platform/emails/sent_log/ so it can be
opened in a browser during local development.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("nexus.email")
LOG_DIR = Path(__file__).parent / "sent_log"


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _save_local(subject: str, to_email: str, html: str) -> str:
    """Save email as an .html file for local preview. Returns the file path."""
    _ensure_log_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(c if c.isalnum() else "_" for c in to_email[:24])
    path = LOG_DIR / f"{ts}_{safe}.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    from_email: str | None = None,
    from_name: str = "NexusAgent Platform",
) -> dict:
    """Send an HTML email. Returns a result dict with delivery info."""
    from_email = from_email or os.environ.get("EMAIL_FROM", "noreply@nexus-platform.com")

    # ── Try SendGrid ─────────────────────────────────────────────────────
    sg_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if sg_key:
        try:
            import sendgrid  # type: ignore
            from sendgrid.helpers.mail import Content, Email, Mail, To  # type: ignore

            sg = sendgrid.SendGridAPIClient(api_key=sg_key)
            message = Mail(
                from_email=Email(from_email, from_name),
                to_emails=To(to_email, to_name),
                subject=subject,
                html_content=Content("text/html", html_body),
            )
            response = sg.client.mail.send.post(request_body=message.get())
            path = _save_local(subject, to_email, html_body)
            logger.info("Email sent via SendGrid to %s (status %s)", to_email, response.status_code)
            return {"provider": "sendgrid", "status_code": response.status_code,
                    "to": to_email, "subject": subject, "local_preview": path}
        except Exception as exc:
            logger.warning("SendGrid failed (%s), trying SMTP", exc)

    # ── Try SMTP ─────────────────────────────────────────────────────────
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    if smtp_host:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_pass = os.environ.get("SMTP_PASS", "")
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = f"{to_name} <{to_email}>"
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls()
                if smtp_user:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(from_email, to_email, msg.as_string())
            path = _save_local(subject, to_email, html_body)
            logger.info("Email sent via SMTP to %s", to_email)
            return {"provider": "smtp", "to": to_email, "subject": subject, "local_preview": path}
        except Exception as exc:
            logger.warning("SMTP failed (%s), falling back to log-only", exc)

    # ── Log-only fallback ────────────────────────────────────────────────
    path = _save_local(subject, to_email, html_body)
    logger.info("Email (log-only) to=%s subject='%s' preview=%s", to_email, subject, path)
    return {"provider": "log_only", "to": to_email, "subject": subject, "local_preview": path}
