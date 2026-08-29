"""Every route reaches its tenant, or says here why it does not.

WHY THIS FILE EXISTS

Multi-tenancy is enforced in the application, not the database. There is no
Postgres row-level security here, so a query that forgets its workspace join is
served happily and silently: no error, no log line, and the first person to find
out is a customer looking at another company's documents.

The risk was never the routes that exist. Those are reviewed and covered by the
refusal tests in test_route_authorization.py and test_access_control.py. The risk
is the NEXT route, written in a hurry, that queries `chunks` or `files` without
reaching a workspace. Nothing structural stops it and no existing test fails.

So this one enumerates every handler in api/routes and fails when a new one
neither performs a recognised tenant check nor appears in EXEMPT below. Adding a
route that needs no check is fine; adding it without saying why is what this
stops.

WHAT THIS IS NOT

It reads source, not behaviour. It proves a handler CONSULTS the tenant boundary,
not that it consults it correctly, and it is no substitute for the refusal tests
that actually drive the API and assert a 403. It is a smoke alarm, not a
sprinkler. Real row-level security would be the sprinkler; see the overview.
"""
import re
import pathlib

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "routes"

# The recognised ways a handler reaches its tenant. All of them resolve, one way
# or another, to "which workspaces or organizations may this caller touch".
TENANT_CHECKS = (
    "assert_workspace_capability",
    "assert_organization_capability",
    "accessible_workspace_ids",
    "check_can_read_workspace",
    "check_can_upload_to_workspace",
    "_authorized_draft",
    "_may_see_file",
    "_reachable_workspaces",
    "_billing_organization_id",
    "org_repo.get_role",
    "user_owns_chat_history",
    "assert_can_create_doc",
)

# Routes that legitimately have no tenant to check. Each one states why, because
# an unexplained entry here is how this test gets quietly defeated.
EXEMPT = {
    # No tenant data: a static price list and a signup.
    ("subscriptions.py", "list_plans"): "public plan list, no customer data",
    ("users.py", "create_user"): "signup, before any organization exists",
    ("users.py", "delete_user"): "acts on the authenticated caller alone",
    ("users.py", "get_deletion_impact"): (
        "reads only the caller's own memberships, resolved from their user_id; "
        "names no organization it was not already a member of"
    ),
    ("users.py", "get_user_quota"): "reads the caller's own quota",

    # Scoped by the authenticated user by construction: they return only what
    # that user belongs to, so there is no id to authorize against.
    ("organizations.py", "list_my_organizations"): "returns the caller's own memberships",
    ("workspaces.py", "list_workspaces"): "returns the caller's own workspaces",
    ("histories.py", "create_history"): "creates for the caller",
    ("histories.py", "delete_all_user_histories"): "deletes the caller's own",
    ("histories.py", "delete_specific_history_messages"): "authorized inside the repository by user_id",

    # Signed or shared-secret callers, not customer sessions.
    ("subscriptions.py", "webhook"): "Stripe webhook, signature verified",
    ("sendgrid_events.py", "sendgrid_events"): "SendGrid webhook, signature verified",
    ("internal.py", "notify_client_endpoint"): "internal, shared-secret verified",
    ("internal.py", "eval_query_endpoint"): "internal, shared-secret verified",

    # The authorization server. Every one of these runs before any workspace is
    # named, and the one step that DOES name one, `approve`, is absent from this
    # list on purpose: it calls accessible_workspace_ids, because deciding which
    # workspace a grant reaches is the whole job of the consent screen.
    ("oauth.py", "protected_resource_metadata"): "public discovery document, no customer data",
    ("oauth.py", "authorization_server_metadata"): "public discovery document, no customer data",
    ("oauth.py", "register_client"): (
        "dynamic client registration, unauthenticated by design; a registered "
        "client reaches nothing until a person approves a workspace for it"
    ),
    ("oauth.py", "authorize"): (
        "validates the request and redirects to the consent screen; names no "
        "workspace and reads no tenant data"
    ),
    ("oauth.py", "token"): (
        "exchanges a code or a refresh token for the grant already approved; "
        "the workspace was decided at consent and is carried on the code"
    ),

    # Deliberately reachable without membership: that is the point of an invite.
    ("workspaces.py", "get_invite_info"): "invite token IS the authorization",
    ("workspaces.py", "accept_invite"): "invite token IS the authorization",

    # Billing acts on the caller's own organization, resolved from the session.
    ("subscriptions.py", "subscription_status"): "resolves the caller's own organization",
    ("subscriptions.py", "cancel_sub"): "acts on the caller's own subscription",
    ("subscriptions.py", "create_setup_intent"): "acts on the caller's own subscription",
    ("subscriptions.py", "update_payment"): "acts on the caller's own subscription",

    # Analytics carries no document or conversation content.
    ("analytics.py", "receive_analytics"): "event ingestion, no tenant data returned",
    ("analytics.py", "analytics_dashboard"): "aggregate only, no tenant data returned",
}


def _handlers():
    """Every route handler, as (file, verb, path, function name, body)."""
    for path in sorted(ROUTES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text()
        chunks = re.split(r"\n(?=@[a-z_]*router\.)", src)
        for chunk in chunks[1:]:
            m = re.match(r"@[a-z_]*router\.(get|post|patch|put|delete)\(\s*[\"']([^\"']*)", chunk)
            if not m:
                continue
            fn = re.search(r"async def (\w+)|^def (\w+)", chunk, re.M)
            if not fn:
                continue
            name = fn.group(1) or fn.group(2)
            # The body runs from the handler's own `def` to the next top-level
            # `def` or decorator. Splitting the whole chunk on `def ` truncated
            # every body to the decorator line, so every route looked unscoped:
            # the first version of this test failed on 30 handlers that were
            # perfectly fine.
            after_def = chunk[fn.start():]
            body = re.split(r"\n(?=@[a-z_]*router\.|async def |def |class )",
                            after_def, maxsplit=1)[0]
            yield path.name, m.group(1).upper(), m.group(2), name, body


def test_there_are_routes_to_check():
    """A regex that silently matches nothing would make this file always pass."""
    found = list(_handlers())
    assert len(found) > 30, f"only found {len(found)} handlers; the parser is broken"


def test_every_route_reaches_its_tenant_or_is_listed_as_not_needing_to():
    unscoped = []
    for filename, verb, path, name, body in _handlers():
        if (filename, name) in EXEMPT:
            continue
        if any(check in body for check in TENANT_CHECKS):
            continue
        unscoped.append(f"{filename}::{name}  ({verb} {path})")

    assert not unscoped, (
        "These routes neither reach their tenant nor are listed as not needing to.\n\n"
        + "\n".join(f"  {u}" for u in unscoped)
        + "\n\nThere is no row-level security in this database, so a route that does "
          "not reach its workspace or organization returns another company's rows "
          "with no error and no log line.\n"
          "Either add one of: " + ", ".join(TENANT_CHECKS) + "\n"
          "or add it to EXEMPT in this file WITH the reason it needs none."
    )


def test_the_exempt_list_has_no_stale_entries():
    """An exemption for a route that no longer exists hides the next one that
    takes its name."""
    real = {(f, n) for f, _, _, n, _ in _handlers()}
    stale = [f"{f}::{n}" for (f, n) in EXEMPT if (f, n) not in real]
    assert not stale, "EXEMPT lists routes that no longer exist: " + ", ".join(stale)


def test_every_exemption_gives_a_reason():
    empty = [f"{f}::{n}" for (f, n), why in EXEMPT.items() if not why.strip()]
    assert not empty, "exemptions without a reason: " + ", ".join(empty)
