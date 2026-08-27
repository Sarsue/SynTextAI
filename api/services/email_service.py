"""Transactional email via SendGrid."""
import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import ClickTracking, Mail, TrackingSettings

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
        # The link, plainly, and nothing to press. No em dashes, per the house
        # style for anything a customer reads.
        #
        # This anchor used to be a styled button, and the styling was blamed on
        # 2026-08-03 for a report of "this site doesn't support a secure
        # connection". That was the wrong diagnosis and the report stayed true
        # for another three weeks. See the tracking_settings block below for
        # what was actually happening: SendGrid rewrites every href here no
        # matter how it is dressed, so no amount of markup was ever going to
        # change the outcome.
        html_content=f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 32px;">
            <h2 style="color: #0062b1;">You've been invited</h2>
            <p>{inviter_name} has invited you to <strong>{workspace_name}</strong> on Syntext, your team's shared knowledge base.</p>
            <p>Open this link to accept:</p>
            <p style="word-break: break-all; font-size: 15px;">
                <a href="{invite_url}" style="color: #0062b1;">{invite_url}</a>
            </p>
            <p style="color: #888; font-size: 13px;">This invite expires in 7 days.<br>
               If you weren't expecting this, you can ignore it.</p>
        </div>
        """,
        plain_text_content=(
            f"{inviter_name} has invited you to {workspace_name} on Syntext, "
            "your team's shared knowledge base.\n\n"
            f"Open this link to accept:\n{invite_url}\n\n"
            "This invite expires in 7 days. If you weren't expecting this, you can ignore it.\n"
        ),
    )

    # Click tracking OFF, and this is load-bearing rather than a preference.
    #
    # SendGrid rewrites every link in the message to
    # http://url639.syntextai.com/ls/click?upn=..., its branded link domain,
    # which is a CNAME to sendgrid.net. That hop resolves correctly over HTTP
    # and preserves the #/invite/<token> fragment, so the destination was never
    # the problem.
    #
    # The problem is that api/app.py sends
    # Strict-Transport-Security: max-age=31536000; includeSubDomains, and
    # includeSubDomains covers url639.syntextai.com. The browser upgrades
    # SendGrid's http:// link to https://, SendGrid holds no certificate
    # covering that name, and HSTS removes the "proceed anyway" escape. Chrome
    # shows NET::ERR_CERT_COMMON_NAME_INVALID and the invite is unreachable.
    #
    # Broken since HSTS shipped in 1e9d746 on 2026-07-29. It looked
    # intermittent because HSTS is trust-on-first-use and we do not set
    # preload: it only bites once that browser has seen syntextai.com, which an
    # invitee who looked the product up first has and a total stranger has not.
    #
    # enable_text as well as enable: they are separate settings at SendGrid and
    # the plain-text part carries the same link.
    #
    # Turning this off here rather than in the SendGrid dashboard keeps it in
    # the repo, where test_email_service.py holds it. A dashboard toggle is one
    # click from silently undoing this, and nothing would fail until an invite
    # went unanswered.
    message.tracking_settings = TrackingSettings(
        click_tracking=ClickTracking(enable=False, enable_text=False)
    )

    sg = SendGridAPIClient(cfg["api_key"])
    response = sg.send(message)
    # SendGrid answers 202 for an accepted send. Anything else is a refusal —
    # most often an unverified from-address — and must not read as success.
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned {response.status_code}: {getattr(response, 'body', b'')!r}")

    logger.info(f"Invite email sent to {to_email} for workspace '{workspace_name}'")
    return invite_url
