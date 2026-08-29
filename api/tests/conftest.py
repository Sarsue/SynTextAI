"""Shared fixtures for the integration tests.

These run against a real Postgres, not a mock. The rules under test are
expressed as SQL joins, so a mocked session would only prove the mock behaves
as written. Point them at the local stack:

    docker compose --env-file .env.dev -f docker-compose.local.yml exec \
        syntextaiapp python -m pytest api/tests -v

Every test creates its own organization and users and removes them afterwards,
so a run leaves no trace in whatever database it was pointed at.
"""
import os
import subprocess
import uuid
from pathlib import Path

# THE SUITE GETS ITS OWN DATABASE, AND THIS IS NOT TIDINESS
#
# The worker container polls the same job queue the tests enqueue into, and
# claims their jobs. Its log during a run: run_type "ingest_file" against test
# user ids, "Starting processing for file: good-one.pdf", which is a fixture
# filename, and "File with ID 8215 not found" where the tenant fixture had torn
# the row down mid-flight. 1364 lines in forty minutes.
#
# Mostly it failed harmlessly. Once in about six runs it did not:
# "Failed to create file record for file_id: 8212" is the worker creating a
# files row, and a row it created carried a name a test was about to reuse, so
# test_a_draft_can_be_approved_again_if_the_document_was_deleted got a 409 where
# it expected a 202. That test is correct. It was losing a race with another
# process.
#
# Pointing the suite at its own database removes the race instead of timing
# around it. The worker reads DATABASE_NAME too, and it is not this one.
#
# Set TEST_DATABASE_NAME to override. Never point it at a database anything else
# is using: the tenant fixture deletes what it made, and a worker mid-flight in
# the same database is exactly what this exists to prevent.
os.environ["DATABASE_NAME"] = os.getenv("TEST_DATABASE_NAME", "syntextai_test")

import pytest
import pytest_asyncio

from api.models.async_db import get_database_url
from api.repositories.repository_manager import RepositoryManager


def _ensure_test_database() -> None:
    """Create the test database if it is missing, then bring it to head.

    Runs once per session. `alembic upgrade head` is a no-op when it is already
    there, which is the common case, so the cost is one connection.
    """
    import asyncio

    import asyncpg

    name = os.environ["DATABASE_NAME"]
    admin = {
        "user": os.getenv("DATABASE_USER", "postgres"),
        "password": os.getenv("DATABASE_PASSWORD", "password"),
        "host": os.getenv("DATABASE_HOST", "localhost"),
        "port": int(os.getenv("DATABASE_PORT", "5432")),
    }

    async def create_if_missing() -> None:
        conn = await asyncpg.connect(database="postgres", **admin)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", name
            )
            if not exists:
                # Identifier, so it cannot be a bound parameter. name comes from
                # our own environment, not from anything a request supplies.
                await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

    asyncio.run(create_if_missing())

    api_dir = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=api_dir,
        check=True,
        capture_output=True,
    )


_ensure_test_database()


@pytest.fixture
def store() -> RepositoryManager:
    """A repository manager per test.

    Deliberately not session-scoped. The async engine binds its connection pool
    to the event loop that created it, and pytest-asyncio gives each test a
    fresh loop, so a shared manager fails on every test after the first with
    connections belonging to a closed loop.
    """
    return RepositoryManager(database_url=get_database_url())


@pytest_asyncio.fixture(loop_scope="session")
async def tenant(store):
    """An organization with an owner, torn down afterwards.

    Emails are uniquely suffixed so a failed run cannot collide with the next
    one on the users table's unique constraint.
    """
    tag = uuid.uuid4().hex[:8]
    created_users = []

    async def make_user(name: str) -> int:
        email = f"{name}-{tag}@test.invalid"
        await store.user_repo.add_user(email, f"{name}-{tag}")
        uid = await store.user_repo.get_user_id_from_email(email)
        created_users.append(uid)
        return uid

    owner_id = await make_user("owner")
    org_id = await store.org_repo.create_organization(f"Test Co {tag}", owner_id)

    class Tenant:
        org = org_id
        owner = owner_id
        new_user = staticmethod(make_user)

        @staticmethod
        async def workspace(name: str) -> int:
            return await store.workspace_repo.create_workspace(user_id=owner_id, name=name)

        @staticmethod
        async def member(name: str = "staff", scope: str = "workspace", workspaces=None) -> int:
            uid = await make_user(name)
            await store.org_repo.add_member(org_id, uid, role="staff")
            await store.org_repo.set_member_access(org_id, uid, scope, workspaces or [])
            return uid

    yield Tenant

    await store.org_repo.delete_organization(org_id)
    for uid in created_users:
        await store.user_repo.delete_user_account(uid)


@pytest_asyncio.fixture(loop_scope="session")
async def client(store):
    """An HTTP client for the real app, with authentication stubbed.

    Authentication is two dependencies: `authenticate_user` for the site, and
    `authenticate_api_caller` for the routes that also accept a credential a
    program holds. Both are overridden, and both resolve to the same stubbed
    person, so a test written before machine credentials existed still exercises
    exactly what it did.

    With one exception, deliberately. A request carrying any credential we issue
    — an API key or an OAuth access token — is handed to the real resolver
    instead of the stub, so ceilings, revocation, rotation and expiry are driven
    end to end against real rows. Stubbing those would leave the entire point of
    the credential untested, and quietly: the request still succeeds, as the
    person the stub returns.

    What is being tested is what the routes do *after* deciding who you are: the
    role checks, the workspace checks, the 403s. Firebase token verification is
    a separate concern and is not exercised here.

    app.state.store is set directly because ASGITransport does not run the
    startup event that normally populates it.
    """
    import httpx
    from fastapi import Depends, Header
    from api.app import app
    from api.core.api_keys import any_prefix
    from api.core.auth import (
        Principal,
        authenticate_api_caller,
        authenticate_user,
        get_store,
    )
    from api.repositories.repository_manager import RepositoryManager

    app.state.store = store

    # Rate limiting off for the suite. It keys on the client address, every
    # test shares one, and a file that walks a flow several times legitimately
    # exceeds a budget written for one customer connecting one app. Leaving it
    # on made eight OAuth tests fail with 429 where they assert 200 and 400,
    # which reads as a broken authorization server rather than a throttle.
    #
    # Nothing here asserts on the budgets, so nothing is lost by this. If a test
    # for them is ever written it should turn the limiter back on for itself.
    from api.core.rate_limit import limiter
    was_enabled = limiter.enabled
    limiter.enabled = False

    current = {"user_id": None}

    async def _as_current_user():
        uid = current["user_id"]
        return {
            "user_id": uid,
            "user_info": {"email": f"user{uid}@test.invalid", "user_id": f"gc-{uid}"},
        }

    async def _as_current_principal(
        authorization: str = Header(None),
        request_store: RepositoryManager = Depends(get_store),
    ):
        """The same person, as a Principal. Unless a real credential is shown."""
        token = (authorization or "").split("Bearer ", 1)[-1].strip()
        # Any credential we issue, not only an API key. Matching `stx_live_`
        # alone meant an OAuth access token fell through to the stub and every
        # request carrying one was served as the signed-in owner: a revoked
        # grant kept working, and the tests that should have caught it passed
        # for the wrong reason.
        if any_prefix(token):
            # Not stubbed. This is the path the feature exists for, and a test
            # that stubbed it would prove only that the stub works.
            return await authenticate_api_caller(
                authorization=authorization, store=request_store
            )
        user = await _as_current_user()
        return Principal(
            user_id=user["user_id"],
            user_info=user["user_info"],
            auth_method="firebase",
        )

    app.dependency_overrides[authenticate_user] = _as_current_user
    app.dependency_overrides[authenticate_api_caller] = _as_current_principal
    app.dependency_overrides[get_store] = lambda: store

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        class Client:
            """Wraps the HTTP client so a test can say who is calling."""
            def as_(self, user_id: int):
                current["user_id"] = user_id
                return http
        yield Client()

    app.dependency_overrides.clear()
    limiter.enabled = was_enabled
