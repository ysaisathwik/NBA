"""Rich HTML email templates for the NEXUS platform.

Cream / off-white design with a navy header and teal accent. Inline styles only so it
renders consistently in Gmail, Outlook, and Apple Mail. One master ``render_email`` plus
six type builders that each return ``(subject, html_body)``.
"""
from __future__ import annotations

import html as html_lib


def render_email(
    *,
    recipient_name: str,
    email_type: str,          # used for the coloured type badge
    headline: str,
    body_paragraphs: list[str],
    detail_rows: list[tuple[str, str]],   # [("Label", "Value"), ...]
    cta_label: str,
    cta_url: str,
    footer_note: str = "",
) -> str:
    """Render a complete HTML email. Returns the full HTML string."""

    TYPE_COLORS = {
        "case_opened":        ("#0891B2", "Case Opened"),
        "engineer_assigned":  ("#F59E0B", "Engineer Assigned"),
        "work_done":          ("#10B981", "Work Completed"),
        "approval_required":  ("#EF4444", "Action Required"),
        "task_assigned":      ("#8B5CF6", "Task Assigned"),
        "resolved":           ("#10B981", "Resolved"),
    }
    badge_color, badge_label = TYPE_COLORS.get(email_type, ("#0891B2", email_type.replace("_", " ").title()))

    detail_html = ""
    if detail_rows:
        rows_html = "".join(
            f"""
            <tr>
              <td style="padding:8px 12px;font-size:13px;color:#6B7280;
                         font-family:Arial,sans-serif;border-bottom:1px solid #E5E0D5;
                         width:38%;vertical-align:top;">{html_lib.escape(str(k))}</td>
              <td style="padding:8px 12px;font-size:13px;color:#1F2937;
                         font-family:Arial,sans-serif;border-bottom:1px solid #E5E0D5;
                         font-weight:600;">{html_lib.escape(str(v))}</td>
            </tr>"""
            for k, v in detail_rows
        )
        detail_html = f"""
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid #E5E0D5;border-radius:8px;
                      background:#FFFFFF;margin:20px 0;">
          <tbody>{rows_html}</tbody>
        </table>"""

    paras_html = "".join(
        f'<p style="margin:0 0 14px 0;font-size:15px;line-height:1.6;'
        f'color:#374151;font-family:Arial,sans-serif;">'
        f'{html_lib.escape(p)}</p>'
        for p in body_paragraphs
    )

    footer_note_html = ""
    if footer_note:
        footer_note_html = (
            f'<p style="margin:16px 0 0 0;font-size:12px;color:#9CA3AF;'
            f'font-family:Arial,sans-serif;font-style:italic;">'
            f'{html_lib.escape(footer_note)}</p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{html_lib.escape(headline)}</title>
</head>
<body style="margin:0;padding:0;background-color:#F5F0E8;font-family:Arial,sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0"
         style="background-color:#F5F0E8;padding:32px 16px;">
    <tr>
      <td align="center">

        <table width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;background:#FAF7F2;
                      border-radius:12px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,0.10);">

          <!-- Header -->
          <tr>
            <td style="background:#0B1C3D;padding:28px 36px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <span style="font-size:26px;font-weight:900;letter-spacing:4px;
                                 color:#FFFFFF;font-family:Arial,sans-serif;">NEXUS</span>
                    <span style="font-size:11px;color:#94A3B8;font-family:Arial,sans-serif;
                                 margin-left:10px;letter-spacing:1px;">
                      INTELLIGENT NEXT BEST ACTION
                    </span>
                  </td>
                  <td align="right">
                    <span style="background:{badge_color};color:#FFFFFF;
                                 font-size:11px;font-weight:700;letter-spacing:0.5px;
                                 padding:5px 12px;border-radius:20px;
                                 font-family:Arial,sans-serif;">
                      {html_lib.escape(badge_label)}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Teal accent line -->
          <tr>
            <td style="background:#0891B2;height:4px;font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:36px 36px 28px 36px;">

              <p style="margin:0 0 6px 0;font-size:13px;color:#9CA3AF;
                        font-family:Arial,sans-serif;letter-spacing:0.3px;">
                Dear {html_lib.escape(recipient_name)},
              </p>

              <h1 style="margin:0 0 20px 0;font-size:22px;font-weight:800;
                         color:#0B1C3D;font-family:Arial,sans-serif;
                         line-height:1.3;">
                {html_lib.escape(headline)}
              </h1>

              {paras_html}

              {detail_html}

              <table width="100%" cellpadding="0" cellspacing="0"
                     style="margin:28px 0 8px 0;">
                <tr>
                  <td align="center">
                    <a href="{cta_url}"
                       style="display:inline-block;background:#0B1C3D;color:#FFFFFF;
                              font-size:15px;font-weight:700;font-family:Arial,sans-serif;
                              text-decoration:none;padding:14px 36px;border-radius:8px;
                              letter-spacing:0.3px;border:2px solid #0B1C3D;">
                      {html_lib.escape(cta_label)} &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:10px 0 0 0;font-size:12px;color:#9CA3AF;
                        font-family:Arial,sans-serif;text-align:center;">
                Button not working?
                <a href="{cta_url}" style="color:#0891B2;text-decoration:underline;">
                  Click here
                </a>
              </p>

              {footer_note_html}

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#F0EBE1;padding:20px 36px;
                       border-top:1px solid #E5E0D5;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:12px;color:#9CA3AF;
                               font-family:Arial,sans-serif;">
                      &copy; NexusAgent — Intelligent Next Best Action · Team HMS
                    </p>
                    <p style="margin:4px 0 0 0;font-size:11px;color:#C4BFB8;
                               font-family:Arial,sans-serif;">
                      This email was sent automatically by the NexusAgent platform.
                      Do not reply directly to this message.
                    </p>
                  </td>
                  <td align="right">
                    <span style="font-size:18px;font-weight:900;letter-spacing:3px;
                                 color:#D1CBC0;font-family:Arial,sans-serif;">N</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>

</body>
</html>"""


def email_case_opened(
    recipient_name: str, case_id: str, event_type: str,
    severity: str, base_url: str, customer_id: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] Your case has been received — {event_type.replace('_', ' ').title()}"
    html = render_email(
        recipient_name=recipient_name,
        email_type="case_opened",
        headline="We've received your report and our team is on it.",
        body_paragraphs=[
            "Thank you for reaching out. NexusAgent has automatically triaged your case "
            "and our specialist agents are analysing it right now.",
            "You'll receive another update once an engineer has been assigned or "
            "action has been authorised.",
        ],
        detail_rows=[
            ("Case ID",    case_id),
            ("Issue type", event_type.replace("_", " ").title()),
            ("Severity",   severity),
            ("Status",     "Under Investigation"),
        ],
        cta_label="Track your case",
        cta_url=f"{base_url}/dashboard?role=customer&tab=cases&sid={case_id}",
        footer_note="If your issue is urgent, please call our 24/7 helpline.",
    )
    return subject, html


def email_engineer_assigned(
    recipient_name: str, case_id: str, asset_id: str,
    work_order_id: str, eta_min: int, base_url: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] Engineer assigned — we're on our way ({asset_id})"
    html = render_email(
        recipient_name=recipient_name,
        email_type="engineer_assigned",
        headline="An engineer has been dispatched to resolve your issue.",
        body_paragraphs=[
            f"Good news — we've assigned a field engineer to your case and they are "
            f"en route with an estimated arrival of {eta_min} minutes.",
            "We'll send you another update once the work is complete.",
        ],
        detail_rows=[
            ("Work Order",     work_order_id),
            ("Asset",          asset_id),
            ("Estimated ETA",  f"{eta_min} minutes"),
            ("Status",         "Engineer En Route"),
        ],
        cta_label="View case status",
        cta_url=f"{base_url}/dashboard?role=customer&tab=cases&sid={case_id}",
    )
    return subject, html


def email_work_done_customer(
    recipient_name: str, case_id: str, asset_id: str,
    action_taken: str, engineer_notes: str,
    work_order_id: str, base_url: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] Work completed on your case — please confirm ({asset_id})"
    html = render_email(
        recipient_name=recipient_name,
        email_type="work_done",
        headline="Our team has completed the work. Is your issue resolved?",
        body_paragraphs=[
            f"Our field team has completed the required work on {asset_id}. "
            "Please let us know whether your issue has been fully resolved.",
            f'Engineer update: "{engineer_notes}"' if engineer_notes else
            "Please log in to confirm resolution or let us know if you need further assistance.",
        ],
        detail_rows=[
            ("Work Order",    work_order_id),
            ("Asset",         asset_id),
            ("Action Taken",  action_taken.replace("_", " ").title()),
            ("Status",        "Awaiting Your Confirmation"),
        ],
        cta_label="Confirm resolution",
        cta_url=f"{base_url}/dashboard?role=customer&tab=feedback&sid={case_id}",
        footer_note="Clicking the button above will take you to a short confirmation screen.",
    )
    return subject, html


def email_approval_required_manager(
    recipient_name: str, case_id: str, action_type: str,
    asset_id: str, urgency: str, confidence: float,
    risk_level: str, base_url: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] ⚡ Authorisation required — {urgency} case ({asset_id})"
    html = render_email(
        recipient_name=recipient_name,
        email_type="approval_required",
        headline=f"A {urgency} case requires your authorisation to proceed.",
        body_paragraphs=[
            f"NexusAgent has analysed the situation and recommends: "
            f"{action_type.replace('_', ' ').title()}. "
            f"Your authorisation is required before the execution team can proceed.",
            "Please log in to review the full recommendation, supporting evidence, "
            "and risk assessment, then issue your authorisation command.",
        ],
        detail_rows=[
            ("Case ID",      case_id),
            ("Asset",        asset_id),
            ("Urgency",      urgency),
            ("Recommended",  action_type.replace("_", " ").title()),
            ("Confidence",   f"{int(confidence * 100)}%"),
            ("Risk Level",   risk_level.upper()),
        ],
        cta_label="Review & Authorise",
        cta_url=f"{base_url}/dashboard?role=manager&tab=authorise&sid={case_id}",
        footer_note=f"This case requires {urgency} response. SLA window is active.",
    )
    return subject, html


def email_task_assigned_engineer(
    recipient_name: str, case_id: str, work_order_id: str,
    asset_id: str, action_type: str, task_description: str,
    urgency: str, base_url: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] \U0001f527 Work order assigned to you — {work_order_id}"
    html = render_email(
        recipient_name=recipient_name,
        email_type="task_assigned",
        headline="A work order has been assigned to you.",
        body_paragraphs=[
            "NexusAgent has assigned the following task to you following manager authorisation. "
            "Please complete the work and mark it as done in your engineer dashboard.",
        ],
        detail_rows=[
            ("Work Order",   work_order_id),
            ("Asset",        asset_id),
            ("Task",         action_type.replace("_", " ").title()),
            ("Description",  task_description),
            ("Priority",     urgency),
        ],
        cta_label="View task & mark complete",
        cta_url=f"{base_url}/dashboard?role=engineer&tab=tasks&sid={case_id}",
        footer_note="Please add completion notes before marking the task as done.",
    )
    return subject, html


def email_task_assigned_operator(
    recipient_name: str, case_id: str, work_order_id: str,
    action_type: str, task_description: str,
    urgency: str, base_url: str,
) -> tuple[str, str]:
    subject = f"[NexusAgent] \U0001f4cb Processing task assigned — {work_order_id}"
    html = render_email(
        recipient_name=recipient_name,
        email_type="task_assigned",
        headline="A processing task has been assigned to you.",
        body_paragraphs=[
            "NexusAgent has assigned the following processing task to you. "
            "Please review the details, complete the required action, "
            "and confirm completion in your operator dashboard.",
        ],
        detail_rows=[
            ("Work Order",  work_order_id),
            ("Task",        action_type.replace("_", " ").title()),
            ("Description", task_description),
            ("Priority",    urgency),
        ],
        cta_label="View task & confirm done",
        cta_url=f"{base_url}/dashboard?role=operator&tab=tasks&sid={case_id}",
        footer_note="Confirmation notes are required before submitting.",
    )
    return subject, html
