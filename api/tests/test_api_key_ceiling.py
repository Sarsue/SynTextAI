"""A credential may narrow what its creator can do. It may never widen it.

WHY THIS FILE EXISTS

An API key is the first credential here that is not a signed-in person, and the
whole safety of it rests on one sentence: the key carries no permission, only a
pointer to whoever created it and a ceiling. Everything is looked up live.

Three ways that could quietly stop being true, and one test each:

  - `limit_workspaces` starts adding instead of intersecting, so a key reaches
    a workspace its creator never had.
  - a scope nobody recognises resolves to "everything" instead of "nothing",
    so a credential written by a newer version gains capabilities here.
  - a route adopts the machine-callable dependency without anyone deciding it
    should, which is the one that cannot be caught by reading this module.

The database-backed refusals (cross-workspace 404, revoked, expired, creator
removed) live in the route tests. These are the invariants that hold with no
database at all, which is why they are cheap enough to never skip.
"""
import pathlib
import re

import pytest

from api.core.api_keys import (
    TOKEN_TAG,
    generate_token,
    hash_token,
    looks_like_api_key,
    matches,
    parse_prefix,
)
from api.core.auth import Principal
from api.core.permissions import Capability, capabilities_for_scopes


def _principal(ceiling=None, caps=None, method="api_key"):
    return Principal(
        user_id=1,
        user_info={},
        auth_method=method,
        credential_id=7 if method == "api_key" else None,
        workspace_ceiling=ceiling,
        capability_ceiling=caps,
    )


# --------------------------------------------------------------------------
# The workspace ceiling narrows and never widens
# --------------------------------------------------------------------------

def test_ceiling_narrows_live_access():
    p = _principal(ceiling=frozenset({3}))
    assert p.limit_workspaces([3, 7, 9]) == [3]


def test_ceiling_cannot_reach_past_live_access():
    """The key names workspace 3. Its creator no longer has it."""
    p = _principal(ceiling=frozenset({3}))
    assert p.limit_workspaces([7, 9]) == []


def test_creator_removed_from_everything_leaves_nothing():
    """Nobody revoked the key. Removing the person was enough."""
    p = _principal(ceiling=frozenset({3}))
    assert p.limit_workspaces([]) == []


def test_a_person_has_no_ceiling():
    p = _principal(method="firebase")
    assert p.limit_workspaces([3, 7, 9]) == [3, 7, 9]
    assert p.is_human


# --------------------------------------------------------------------------
# The capability ceiling sits above the role, not beside it
# --------------------------------------------------------------------------

def test_read_only_key_cannot_delete_even_for_an_owner():
    """The Principal is an owner's. The credential is still read-only."""
    p = _principal(caps=capabilities_for_scopes(["knowledge:read"]))
    assert p.permits(Capability.READ)
    assert not p.permits(Capability.DELETE_DOCUMENT)
    assert not p.permits(Capability.MANAGE_BILLING)
    # And minting more credentials least of all.
    assert not p.permits(Capability.MANAGE_API_KEYS)


def test_unknown_scope_grants_nothing():
    """Fail closed. A scope this code cannot resolve must lose the capability,
    never gain one, or a credential issued by a newer version widens itself."""
    assert capabilities_for_scopes(["knowledge:write-everything"]) == frozenset()
    assert capabilities_for_scopes([]) == frozenset()
    assert capabilities_for_scopes(None) == frozenset()


def test_no_ceiling_permits_everything():
    p = _principal(method="firebase")
    assert p.permits(Capability.DELETE_DOCUMENT)


# --------------------------------------------------------------------------
# The token itself
# --------------------------------------------------------------------------

def test_token_round_trip():
    full, prefix, digest = generate_token()
    assert full.startswith(TOKEN_TAG)
    assert parse_prefix(full) == prefix
    assert matches(full, digest)


def test_a_changed_token_does_not_match():
    full, _, digest = generate_token()
    assert not matches(full + "0", digest)
    assert not matches(full[:-1], digest)


def test_two_tokens_are_not_alike():
    a, prefix_a, _ = generate_token()
    b, prefix_b, _ = generate_token()
    assert a != b and prefix_a != prefix_b


def test_a_firebase_token_is_not_mistaken_for_a_key():
    """Dispatch happens on this test, so it decides which resolver runs."""
    jwt = "eyJhbGciOiJSUzI1NiIsImtpZCI6IjEyMyJ9.eyJzdWIiOiJhYmMifQ.sig"
    assert not looks_like_api_key(jwt)
    assert parse_prefix(jwt) is None


def test_a_malformed_key_is_unparseable_rather_than_fatal():
    assert parse_prefix(TOKEN_TAG) is None
    assert parse_prefix(TOKEN_TAG + "noseparator") is None
    assert parse_prefix("") is None


def test_the_secret_is_not_recoverable_from_the_hash():
    full, _, digest = generate_token()
    assert full not in digest
    assert digest == hash_token(full)
    assert len(digest) == 64


# --------------------------------------------------------------------------
# Only routes that were decided on may take a machine credential
# --------------------------------------------------------------------------

# Every route allowed to accept something other than a signed-in person. This is
# load-bearing, not tidiness. test_every_route_is_scoped.py recognises eleven
# different tenant checks, and only `accessible_workspace_ids` has been taught
# about the ceiling so far. A Principal is safe on a route that intersects with
# it and on no other, so adding a name here means having checked that route's
# own tenant check honours `limit_workspaces`.
MACHINE_CALLABLE = {
    "search.py",
    # Reaches its tenant through the same `accessible_workspace_ids` then
    # `limit_workspaces` pair search does, in `_reachable_workspaces`.
    "mcp.py",
}

ROUTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "routes"


# Adoption is the wiring, not the word. Matching the bare name instead flags any
# module that merely explains the dependency in a comment, which is how the first
# version of this test failed on the very file whose docstring exists to say it
# does NOT use it.
_ADOPTS = re.compile(r"Depends\(\s*authenticate_api_caller\s*\)")


def test_only_allowlisted_routes_accept_a_machine_credential():
    adopted = {
        path.name
        for path in ROUTES_DIR.glob("*.py")
        if _ADOPTS.search(path.read_text())
    }
    unexpected = adopted - MACHINE_CALLABLE
    assert not unexpected, (
        "These routes accept an API key without being on the allowlist: "
        f"{sorted(unexpected)}. Confirm the route's tenant check applies the "
        "workspace ceiling, then add it to MACHINE_CALLABLE."
    )


def test_minting_a_key_is_not_something_a_key_can_do():
    """A credential that can create a credential cannot be revoked."""
    source = (ROUTES_DIR / "api_keys.py").read_text()
    assert not _ADOPTS.search(source)
    assert re.search(r"Depends\(\s*authenticate_user\s*\)", source)
