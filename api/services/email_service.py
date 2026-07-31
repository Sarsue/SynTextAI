"""Transactional email via SendGrid."""
import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

logger = logging.getLogger(__name__)

# Read at call time, never at import.
#
# These used to be module-level os.getenv() calls, which run when the module is
# first imported — before load_dotenv() has populated the environment from
# /app/.env. Anything set only in the env file therefore read as absent and
# silently took the default: SENDGRID_FROM_EMAIL is set to a verified sender in
# .env.dev, but the service used noreply@syntext.ai and SendGrid rejects an
# unverified from-address, so every invite failed. Only SENDGRID_API_KEY worked,
# because docker-compose injects that one into the real process environment.


def _config() -> dict:
    # The invite link has to point at the app the recipient actually uses.
    # The old default was https://app.syntext.ai, which is not this product's
    # domain, so an invite that did send led nowhere. Fall back to the first
    # configured CORS origin, which is by definition a real frontend origin.
    cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    default_app_url = cors_origins[0] if cors_origins else "https://syntextai.com"
    return {
        "api_key": os.getenv("SENDGRID_API_KEY"),
        "from_email": os.getenv("SENDGRID_FROM_EMAIL"),
        "app_url": (os.getenv("APP_URL") or default_app_url).rstrip("/"),
    }


def app_url() -> str:
    """Frontend base URL used to build links sent to users."""
    return _config()["app_url"]


class EmailNotConfigured(RuntimeError):
    """Raised when a send is attempted without the settings to make it."""


def send_workspace_invite(to_email: str, workspace_name: str, token: str, inviter_name: str = "Your team") -> str:
    """Send a workspace invite email.

    Returns the invite URL on success. Raises rather than returning a falsy
    value the caller can ignore: the previous signature returned a bool that
    the route discarded, so a failed send still answered "Invite sent to
    <email>" and the invite existed as a row nobody was ever told about.
    """
    cfg = _config()

    missing = [name for name, value in (
        ("SENDGRID_API_KEY", cfg["api_key"]),
        ("SENDGRID_FROM_EMAIL", cfg["from_email"]),
    ) if not value]
    if missing:
        raise EmailNotConfigured(f"Missing {', '.join(missing)}")

    invite_url = f"{cfg['app_url']}/#/invite/{token}"

    message = Mail(
        from_email=cfg["from_email"],
        to_emails=to_email,
        subject=f"{inviter_name} invited you to join their knowledge base on Syntext",
        html_content=f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 32px;">
            <h2 style="color: #0062b1;">You've been invited</h2>
            <p>{inviter_name} has invited you to <strong>{workspace_name}</strong> on Syntext — your team's shared knowledge base.</p>
            <p>Click the button below to accept the invite and join your team.</p>
            <a href="{invite_url}"
               style="display: inline-block; background: #0062b1; color: white; padding: 12px 24px;
                      border-radius: 6px; text-decoration: none; font-weight: 600; margin: 16px 0;">
                Accept Invite
            </a>
            <p style="color: #888; font-size: 13px;">This invite expires in 7 days.<br>
               If you weren't expecting this, you can ignore it.</p>
            <p style="color: #888; font-size: 12px;">If the button doesn't work, paste this into your browser:<br>
               <span style="word-break: break-all;">{invite_url}</span></p>
        </div>
        """,
    )

    sg = SendGridAPIClient(cfg["api_key"])
    response = sg.send(message)
    # SendGrid answers 202 for an accepted send. Anything else is a refusal —
    # most often an unverified from-address — and must not read as success.
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned {response.status_code}: {getattr(response, 'body', b'')!r}")

    logger.info(f"Invite email sent to {to_email} for workspace '{workspace_name}'")
    return invite_url
