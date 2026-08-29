"""An OAuth 2.1 authorization server, so connecting is a click.

WHY THIS EXISTS

An API key works and is the wrong shape for the customer this is sold to. It
asks somebody at a dental practice to generate a secret, keep it secret, and
paste it into a configuration file they have never opened. This asks them to
press Allow.

THE FLOW, AND WHERE EACH PART LIVES

    client                     here                        the person
    ------                     ----                        ----------
    discovers metadata    ->   /.well-known/...
    registers itself      ->   POST /oauth/register
    opens a browser       ->   GET  /oauth/authorize   ->   consent screen
                                                            (the React app)
                               POST /oauth/authorize   <-   Allow, workspace X
    redirected with code  <-
    exchanges the code    ->   POST /oauth/token

GET /authorize does no authenticating of its own. It validates the request and
sends the browser to the app's consent screen, because the person is signed in
there with Firebase and reproducing that here would mean a second login. The
approval comes back as POST /authorize carrying their ID token, which is the
only step that mints a code.

WHAT THIS SERVER DOES NOT DO

No implicit grant, no password grant, no `plain` PKCE. Each is in some version
of the specification and each hands away the protection the rest of this is
for. S256 only, authorization code only.

Nothing here decides permission. A grant carries a user, a workspace and scopes,
exactly as an API key does, and core/auth resolves it into the same Principal.
"""
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse
import logging
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..core.auth import authenticate_user, get_store
from ..core.permissions import SCOPES
from ..core.rate_limit import limiter
from ..core.urls import public_app_url
from ..repositories.repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

# Two routers. The well-known documents have to sit at the root of the origin
# because that is where a client looks for them; everything else is ordinary
# API surface.
discovery_router = APIRouter(tags=["oauth"])
oauth_router = APIRouter(prefix="/api/v1/oauth", tags=["oauth"])

# The issuer, and the base every advertised endpoint is built from. Read from
# configuration rather than from the request: behind a proxy the Host header is
# whatever the proxy was told to send, and an issuer that changes with the
# request is one a client cannot pin.
_base_url = public_app_url


# Registration is unauthenticated by design, which is what dynamic client
# registration means: nobody pre-registers Claude with every server it might
# connect to. It is also the one endpoint a stranger can write rows through, so
# it gets its own budget.
REGISTRATION_RATE_LIMIT = "5/minute"
TOKEN_RATE_LIMIT = "20/minute"


class RegistrationRequest(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=120)
    redirect_uris: List[str] = Field(..., min_length=1, max_length=10)


class ConsentRequest(BaseModel):
    client_id: str
    redirect_uri: str
    workspace_id: int
    scopes: List[str] = Field(default_factory=lambda: ["knowledge:read"])
    code_challenge: str
    code_challenge_method: str = "S256"
    state: Optional[str] = None


def _safe_redirect(uri: str) -> bool:
    """Whether this is somewhere a code may be sent.

    https anywhere, http only on loopback. A desktop client legitimately
    listens on 127.0.0.1 and cannot have a certificate for it; anything else on
    plain http would put an authorization code on the wire in clear text.
    """
    try:
        parsed = urlparse(uri)
    except Exception:
        return False
    if parsed.scheme == "https":
        return bool(parsed.netloc)
    if parsed.scheme == "http":
        return parsed.hostname in ("127.0.0.1", "localhost", "::1")
    return False


@discovery_router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata() -> JSONResponse:
    """Where a client should go to get a token for this resource. RFC 9728."""
    base = _base_url()
    return JSONResponse(
        {
            "resource": f"{base}/api/v1/mcp",
            "authorization_servers": [base],
            "scopes_supported": sorted(SCOPES.keys()),
            "bearer_methods_supported": ["header"],
        }
    )


@discovery_router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> JSONResponse:
    """What this server supports. RFC 8414.

    The omissions are the interesting part. `code` is the only response type,
    `S256` the only challenge method, and there is no `token` response type or
    `plain` method to fall back to. A client cannot negotiate its way down to
    something weaker than this list.
    """
    base = _base_url()
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/api/v1/oauth/authorize",
            "token_endpoint": f"{base}/api/v1/oauth/token",
            "registration_endpoint": f"{base}/api/v1/oauth/register",
            "scopes_supported": sorted(SCOPES.keys()),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


@oauth_router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(REGISTRATION_RATE_LIMIT)
async def register_client(
    request: Request,
    body: RegistrationRequest,
    store: RepositoryManager = Depends(get_store),
) -> JSONResponse:
    """Dynamic client registration. RFC 7591.

    Deliberately unauthenticated, and deliberately harmless: a registered client
    can do exactly nothing until a signed-in person approves it for a specific
    workspace on the consent screen. What it buys an attacker is a row and a
    name on a screen somebody is about to read.
    """
    for uri in body.redirect_uris:
        if not _safe_redirect(uri):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"redirect_uri must be https, or http on loopback: {uri}",
            )

    client = await store.oauth_repo.register_client(
        client_name=body.client_name.strip(), redirect_uris=body.redirect_uris
    )
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not register that client.",
        )
    logger.info("OAuth client registered")
    return JSONResponse(
        {
            "client_id": client["client_id"],
            "client_name": client["client_name"],
            "redirect_uris": client["redirect_uris"],
            # No secret: this is a public client and PKCE is what proves the
            # token request came from whoever started the authorization.
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=status.HTTP_201_CREATED,
    )


@oauth_router.get("/authorize")
async def authorize(
    request: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "knowledge:read",
    state: Optional[str] = None,
    store: RepositoryManager = Depends(get_store),
):
    """Validate the request, then hand the browser to the consent screen.

    Every refusal here is a plain error rather than a redirect carrying one. A
    request naming an unregistered client or an unregistered redirect has not
    proved where it came from, and bouncing an error to an unverified URL is how
    an open redirect gets built by accident.
    """
    if response_type != "code":
        raise HTTPException(400, "Only response_type=code is supported.")
    if (code_challenge_method or "").upper() != "S256":
        raise HTTPException(400, "Only code_challenge_method=S256 is supported.")
    if not code_challenge:
        raise HTTPException(400, "code_challenge is required.")

    client = await store.oauth_repo.get_client(client_id)
    if client is None:
        raise HTTPException(400, "Unknown client_id.")
    # Exact match, not a prefix or a host comparison. A registered
    # "https://app.example.com/cb" must not authorize
    # "https://app.example.com/cb/../elsewhere".
    if redirect_uri not in client["redirect_uris"]:
        raise HTTPException(400, "redirect_uri was not registered by this client.")

    unknown = [s for s in scope.split() if s not in SCOPES]
    if unknown:
        raise HTTPException(400, f"Unknown scope: {' '.join(unknown)}")

    # The app is a HashRouter, so the consent route lives after the '#'. The
    # query has to sit inside the hash for the router to see it.
    params = {
        "client_id": client_id,
        # Carried through rather than fetched again by the page. It is the name
        # the client registered for itself, which nothing has verified, so the
        # screen shows it beside the redirect host: the name is a claim, the
        # host is where a code would actually go.
        "client_name": client["client_name"],
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if state:
        params["state"] = state
    return RedirectResponse(
        url=f"{_base_url()}/#/oauth/consent?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@oauth_router.post("/authorize")
async def approve(
    body: ConsentRequest,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
) -> JSONResponse:
    """A signed-in person approving one client for one workspace.

    The only step that mints a code, and the only step that consults who the
    person is. The workspace comes from the person, never from the client: a
    client asking for a particular workspace would be choosing what it gets
    access to, which is the decision this screen exists to take away from it.
    """
    user_id = user_data["user_id"]

    client = await store.oauth_repo.get_client(body.client_id)
    if client is None:
        raise HTTPException(400, "Unknown client_id.")
    if body.redirect_uri not in client["redirect_uris"]:
        raise HTTPException(400, "redirect_uri was not registered by this client.")
    if (body.code_challenge_method or "").upper() != "S256":
        raise HTTPException(400, "Only code_challenge_method=S256 is supported.")

    unknown = [s for s in body.scopes if s not in SCOPES]
    if unknown:
        raise HTTPException(400, f"Unknown scope: {' '.join(unknown)}")

    # The person can only grant a workspace they can currently read. Checked
    # here rather than trusted from the form, because the form is a page in a
    # browser and this is the sentence that decides what the token reaches.
    accessible = await store.workspace_repo.accessible_workspace_ids(user_id)
    if body.workspace_id not in accessible:
        raise HTTPException(404, "Workspace not found")

    code = await store.oauth_repo.create_code(
        client_id=body.client_id,
        user_id=user_id,
        workspace_id=body.workspace_id,
        scopes=body.scopes,
        code_challenge=body.code_challenge,
        code_challenge_method="S256",
        redirect_uri=body.redirect_uri,
    )
    if code is None:
        raise HTTPException(500, "Could not complete that authorization.")

    params = {"code": code}
    if body.state:
        params["state"] = body.state
    separator = "&" if "?" in body.redirect_uri else "?"
    logger.info("OAuth authorization approved for workspace %s", body.workspace_id)
    # Returned rather than redirected: the caller is the consent page's fetch,
    # and it does the navigating.
    return JSONResponse({"redirect_to": f"{body.redirect_uri}{separator}{urlencode(params)}"})


def _oauth_error(code: str, description: str) -> JSONResponse:
    # RFC 6749 shape. 400 with an `error` member, which is what a client parses;
    # anything else reads as the server being broken rather than the request
    # being wrong.
    return JSONResponse(
        {"error": code, "error_description": description},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@oauth_router.post("/token")
@limiter.limit(TOKEN_RATE_LIMIT)
async def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    store: RepositoryManager = Depends(get_store),
) -> JSONResponse:
    """Exchange a code, or refresh. Form-encoded, as the specification requires.

    Every refusal is the same `invalid_grant`, whatever went wrong: expired,
    already used, wrong client, wrong redirect, wrong verifier. Telling a caller
    which one it got right is telling it what to try next.
    """
    if grant_type == "authorization_code":
        if not code or not redirect_uri or not code_verifier:
            return _oauth_error(
                "invalid_request",
                "code, redirect_uri and code_verifier are all required.",
            )
        grant = await store.oauth_repo.consume_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        if grant is None:
            return _oauth_error("invalid_grant", "That authorization code is not usable.")
        issued = await store.oauth_repo.issue_tokens(
            client_id=client_id,
            user_id=grant["user_id"],
            workspace_id=grant["workspace_id"],
            scopes=grant["scopes"],
        )
    elif grant_type == "refresh_token":
        if not refresh_token:
            return _oauth_error("invalid_request", "refresh_token is required.")
        issued = await store.oauth_repo.rotate_refresh(refresh_token, client_id)
        if issued is None:
            return _oauth_error("invalid_grant", "That refresh token is not usable.")
    else:
        return _oauth_error(
            "unsupported_grant_type",
            "Only authorization_code and refresh_token are supported.",
        )

    if issued is None:
        return _oauth_error("invalid_grant", "Could not issue a token.")

    return JSONResponse(
        {
            "access_token": issued["access_token"],
            "token_type": "Bearer",
            "expires_in": issued["expires_in"],
            "refresh_token": issued["refresh_token"],
            "scope": " ".join(issued["scopes"]),
        }
    )
