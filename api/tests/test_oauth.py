"""The authorization server: what it grants, and everything it refuses.

The flow has four steps and only one of them decides anything. Registration
grants nothing, /authorize grants nothing, /token only hands over what was
already approved. The consent POST is the single place a workspace is attached
to a client, and it is the only step that asks who the person is.

So most of this file is refusal, and the refusals that matter most are the ones
that would still look like a working integration if they were missing: a code
redeemed twice, a verifier that does not match, a redirect that changed between
the two steps.
"""
import base64
import hashlib
import secrets

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="session")

REGISTER = "/api/v1/oauth/register"
AUTHORIZE = "/api/v1/oauth/authorize"
TOKEN = "/api/v1/oauth/token"
REDIRECT = "http://127.0.0.1:33418/callback"


def pkce() -> tuple:
    """A verifier and its S256 challenge, made the way a client makes them."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def register(client, user_id: int, redirect: str = REDIRECT) -> str:
    response = await client.as_(user_id).post(
        REGISTER, json={"client_name": "Claude", "redirect_uris": [redirect]}
    )
    assert response.status_code == 201
    return response.json()["client_id"]


async def approve(client, user_id: int, client_id: str, workspace: int, challenge: str,
                  redirect: str = REDIRECT) -> str:
    """Run the consent step and pull the code out of the redirect."""
    response = await client.as_(user_id).post(
        AUTHORIZE,
        json={
            "client_id": client_id,
            "redirect_uri": redirect,
            "workspace_id": workspace,
            "scopes": ["knowledge:read"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["redirect_to"].split("code=")[1].split("&")[0]


async def exchange(client, user_id: int, client_id: str, code: str, verifier: str,
                   redirect: str = REDIRECT):
    return await client.as_(user_id).post(
        TOKEN,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

async def test_the_resource_says_where_to_get_a_token(client, tenant):
    response = await client.as_(tenant.owner).get(
        "/.well-known/oauth-protected-resource"
    )
    body = response.json()
    assert body["resource"].endswith("/api/v1/mcp")
    assert body["authorization_servers"]


async def test_the_server_advertises_only_what_it_will_accept(client, tenant):
    """The omissions are the point: nothing here lets a client negotiate down."""
    body = (await client.as_(tenant.owner).get(
        "/.well-known/oauth-authorization-server"
    )).json()

    assert body["response_types_supported"] == ["code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "plain" not in body["code_challenge_methods_supported"]
    assert "token" not in body["response_types_supported"]
    assert "password" not in body["grant_types_supported"]


async def test_an_unauthenticated_mcp_call_says_where_to_get_a_token(client, tenant):
    """Without this header a client meets a 401 and stops, instead of starting
    the flow that would fix it."""
    response = await client.as_(tenant.owner).post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer stx_live_deadbeefdead_" + "0" * 64},
    )
    assert response.status_code == 401
    assert "oauth-protected-resource" in response.headers.get("www-authenticate", "")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def test_a_client_can_register_itself(client, tenant):
    """Unauthenticated by design, and harmless: it reaches nothing until
    somebody approves a workspace for it."""
    client_id = await register(client, tenant.owner)
    assert client_id.startswith("stx_client_")


async def test_a_plain_http_redirect_off_loopback_is_refused(client, tenant):
    """A code on the wire in clear text. Loopback is the exception because a
    desktop client cannot have a certificate for 127.0.0.1."""
    response = await client.as_(tenant.owner).post(
        REGISTER,
        json={"client_name": "Sketchy", "redirect_uris": ["http://evil.example.com/cb"]},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------

async def test_authorize_sends_the_browser_to_the_consent_screen(client, tenant):
    client_id = await register(client, tenant.owner)
    _, challenge = pkce()

    response = await client.as_(tenant.owner).get(
        AUTHORIZE,
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/oauth/consent" in response.headers["location"]
    # The name travels with it, so the screen can show what is asking.
    assert "client_name" in response.headers["location"]


async def test_an_unregistered_redirect_is_refused_not_redirected(client, tenant):
    """Bouncing an error to an unverified URL is how an open redirect gets
    built by accident."""
    client_id = await register(client, tenant.owner)
    _, challenge = pkce()

    response = await client.as_(tenant.owner).get(
        AUTHORIZE,
        params={
            "client_id": client_id,
            "redirect_uri": "https://attacker.example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


async def test_plain_pkce_is_refused(client, tenant):
    """`plain` makes the challenge equal the verifier, so an intercepted
    authorization request carries everything needed to redeem its own code."""
    client_id = await register(client, tenant.owner)

    response = await client.as_(tenant.owner).get(
        AUTHORIZE,
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": "whatever",
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


async def test_a_person_cannot_grant_a_workspace_they_cannot_read(
    store, tenant, client
):
    """The workspace comes from the person, and is checked against what they
    can currently read rather than trusted from the form."""
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Private")
    outsider = await tenant.new_user("outsider")
    _, challenge = pkce()

    response = await client.as_(outsider).post(
        AUTHORIZE,
        json={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "workspace_id": workspace,
            "scopes": ["knowledge:read"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

async def test_a_code_becomes_a_working_access_token(store, tenant, client):
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Granted")
    verifier, challenge = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    response = await exchange(client, tenant.owner, client_id, code, verifier)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].startswith("stx_at_")
    assert body["refresh_token"].startswith("stx_rt_")
    assert body["expires_in"] > 0

    # And it authenticates, as the person who approved it.
    listed = await client.as_(tenant.owner).post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert listed.status_code == 200
    assert listed.json()["result"]["tools"]


async def test_a_wrong_verifier_is_refused(store, tenant, client):
    """PKCE doing its job: the code alone is not enough."""
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Pkce")
    _, challenge = pkce()
    other_verifier, _ = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    response = await exchange(client, tenant.owner, client_id, code, other_verifier)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_a_replayed_code_withdraws_the_tokens_it_already_made(
    store, tenant, client
):
    """The important one. A code presented twice means somebody else may have
    held it, so refusing the second request while leaving the first request's
    tokens alive would protect nothing."""
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Replay")
    verifier, challenge = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    first = await exchange(client, tenant.owner, client_id, code, verifier)
    access = first.json()["access_token"]

    # It works.
    ok = await client.as_(tenant.owner).post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert ok.status_code == 200

    replay = await exchange(client, tenant.owner, client_id, code, verifier)
    assert replay.status_code == 400

    # And now the token from the first, legitimate exchange is dead too.
    after = await client.as_(tenant.owner).post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert after.status_code == 401


async def test_a_code_redeemed_against_a_different_redirect_is_refused(
    store, tenant, client
):
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Swapped")
    verifier, challenge = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    response = await exchange(
        client, tenant.owner, client_id, code, verifier,
        redirect="http://127.0.0.1:33418/somewhere-else",
    )

    assert response.status_code == 400


async def test_a_refresh_rotates_and_retires_the_old_one(store, tenant, client):
    """A stolen refresh token is good for one use, and then the real client's
    next refresh fails loudly instead of silently sharing the grant."""
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Rotate")
    verifier, challenge = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    first = (await exchange(client, tenant.owner, client_id, code, verifier)).json()

    refreshed = await client.as_(tenant.owner).post(
        TOKEN,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": first["refresh_token"],
        },
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != first["refresh_token"]

    reused = await client.as_(tenant.owner).post(
        TOKEN,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": first["refresh_token"],
        },
    )
    assert reused.status_code == 400


async def test_an_unsupported_grant_type_is_refused(client, tenant):
    response = await client.as_(tenant.owner).post(
        TOKEN,
        data={
            "grant_type": "password",
            "client_id": "anything",
            "username": "someone",
            "password": "hunter2",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


# ---------------------------------------------------------------------------
# The grant on the Connections screen
# ---------------------------------------------------------------------------

async def test_a_grant_appears_beside_the_api_keys(store, tenant, client):
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Both")
    verifier, challenge = pkce()

    await client.as_(tenant.owner).post(
        f"/api/v1/workspaces/{workspace}/api-keys", json={"name": "A script"}
    )
    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    await exchange(client, tenant.owner, client_id, code, verifier)

    rows = (await client.as_(tenant.owner).get(
        f"/api/v1/workspaces/{workspace}/api-keys"
    )).json()

    kinds = {r["kind"] for r in rows}
    assert kinds == {"api_key", "oauth"}
    grant = next(r for r in rows if r["kind"] == "oauth")
    assert grant["name"] == "Claude"


async def test_revoking_a_grant_kills_its_access_token(store, tenant, client):
    client_id = await register(client, tenant.owner)
    workspace = await tenant.workspace("Cutoff")
    verifier, challenge = pkce()

    code = await approve(client, tenant.owner, client_id, workspace, challenge)
    access = (await exchange(client, tenant.owner, client_id, code, verifier)).json()[
        "access_token"
    ]

    rows = (await client.as_(tenant.owner).get(
        f"/api/v1/workspaces/{workspace}/api-keys"
    )).json()
    grant = next(r for r in rows if r["kind"] == "oauth")

    deleted = await client.as_(tenant.owner).delete(
        f"/api/v1/workspaces/{workspace}/api-keys/{grant['id']}?kind=oauth"
    )
    assert deleted.status_code == 204

    after = await client.as_(tenant.owner).post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert after.status_code == 401


async def test_revoking_needs_to_know_which_list_the_row_came_from(
    store, tenant, client
):
    """The two tables number rows independently, so an id alone is ambiguous and
    falling through from one to the other would revoke the wrong connection."""
    workspace = await tenant.workspace("Kinds")
    response = await client.as_(tenant.owner).delete(
        f"/api/v1/workspaces/{workspace}/api-keys/1?kind=guess"
    )
    assert response.status_code == 422
