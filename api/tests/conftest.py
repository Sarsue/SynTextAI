"""Shared fixtures for the integration tests.

These run against a real Postgres, not a mock. The rules under test are
expressed as SQL joins, so a mocked session would only prove the mock behaves
as written. Point them at the local stack:

    docker compose --env-file .env.dev -f docker-compose.local.yml exec \
        syntextaiapp python -m pytest api/tests -v

Every test creates its own organization and users and removes them afterwards,
so a run leaves no trace in whatever database it was pointed at.
"""
import uuid

import pytest
import pytest_asyncio

from api.models.async_db import get_database_url
from api.repositories.repository_manager import RepositoryManager


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

    Authentication is one dependency now, so one override covers every router.
    What is being tested is what the routes do *after* deciding who you are: the
    role checks, the workspace checks, the 403s. Firebase token verification is
    a separate concern and is not exercised here.

    app.state.store is set directly because ASGITransport does not run the
    startup event that normally populates it.
    """
    import httpx
    from api.app import app
    from api.core.auth import authenticate_user, get_store

    app.state.store = store

    current = {"user_id": None}

    async def _as_current_user():
        uid = current["user_id"]
        return {
            "user_id": uid,
            "user_info": {"email": f"user{uid}@test.invalid", "user_id": f"gc-{uid}"},
        }

    app.dependency_overrides[authenticate_user] = _as_current_user
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
