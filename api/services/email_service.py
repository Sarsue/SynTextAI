"""Transactional email via SendGrid."""
import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "noreply@syntext.ai")
APP_URL = os.getenv("APP_URL", "https://app.syntext.ai")


def send_workspace_invite(to_email: str, workspace_name: str, token: str, inviter_name: str = "Your team") -> bool:
    """Send a workspace invite email. Returns True on success."""
    if not SENDGRID_API_KEY:
        logger.warning("SENDGRID_API_KEY not set — skipping invite email")
        return False

    invite_url = f"{APP_URL}/#/invite/{token}"

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject=f"{inviter_name} invited you to join their knowledge base on Syntext",
        html_content=f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 32px;">
            <h2 style="color: #0078d4;">You've been invited</h2>
            <p>{inviter_name} has invited you to <strong>{workspace_name}</strong> on Syntext — your team's shared knowledge base.</p>
            <p>Click the button below to accept the invite and join your team.</p>
            <a href="{invite_url}"
               style="display: inline-block; background: #0078d4; color: white; padding: 12px 24px;
                      border-radius: 6px; text-decoration: none; font-weight: 600; margin: 16px 0;">
                Accept Invite
            </a>
            <p style="color: #888; font-size: 13px;">This invite expires in 7 days.<br>
               If you weren't expecting this, you can ignore it.</p>
        </div>
        """,
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        logger.info(f"Invite email sent to {to_email} for workspace '{workspace_name}'")
        return True
    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}")
        return False
